"""Audit_Console_Avril_2026.xlsx — version filtrée (clients listés par le service factu)."""
import json, os, re
from pathlib import Path
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment

TEMP = os.environ.get('TEMP') or '/tmp'
MSP = json.loads(Path(TEMP, 'msp_data.json').read_text(encoding='utf-8'))

# Index
recon = {r['code']: r for r in MSP.get('reconciliation', []) if r.get('code')}
contrats_by_code = {}
for c in MSP.get('reconciliation_par_contrat', []):
    code = c.get('code')
    if code: contrats_by_code.setdefault(code, []).append(c)
datto_by_code = {}
for s in MSP.get('raw_sources', {}).get('datto', []):
    c = str(s.get('artis_code') or '').lstrip('0') or '0'
    if c == '0' or not c: continue
    datto_by_code.setdefault(c, []).append(s)
m365_by_code = {}
for s in MSP.get('raw_sources', {}).get('m365', []):
    c = str(s.get('artis_code') or '').lstrip('0') or '0'
    if c == '0' or not c: continue
    m365_by_code.setdefault(c, []).append(s)

def find_code_by_name(name_pattern):
    pat = re.compile(name_pattern, re.IGNORECASE)
    for code, r in recon.items():
        if pat.search(r.get('name') or ''):
            return code
    return None

# === Whitelists service facturation ===
DATTO_CODES = ['30857','30428','9974','346','9917','12534','12579','12726','5422','14531',
               '13899','19041','5989','5796','12955','388','12461','12566','9043','19043',
               '13132','12514','12520']
# XERFI DGT
xerfi = find_code_by_name(r'XERFI\s*DGT|XERFI')
if xerfi: DATTO_CODES.append(xerfi)

M365_CODES = ['19036','30629','14983','10677','30700','4444','1945','30548','10943','8231',
              '30703','9758','31017','12534','12531','12503','26183','12955','15566','8816',
              '5831','6129','15043','13084']
# NORTH COMMUNICATION
north = find_code_by_name(r'NORTH\s*COMM')
if north: M365_CODES.append(north)

DATTO_CODES = list(dict.fromkeys(DATTO_CODES))  # dedupe
M365_CODES  = list(dict.fromkeys(M365_CODES))

# Excel context (col B + C par code)
src = openpyxl.load_workbook(Path.cwd() / 'Etat console AVRIL 2026.xlsx', data_only=True)
ws_src = src['avril']
sections, current = {}, None
for row in ws_src.iter_rows(values_only=True):
    if all(c is None for c in row): continue
    c0 = row[0]
    if c0 and not row[1] and not row[2] and isinstance(c0, str) and c0.strip().upper() in ('DATTO','M365'):
        current = c0.strip().upper(); sections[current] = []; continue
    if current: sections[current].append(row[:5])

def extract_code(label):
    if not label: return None
    s = str(label)
    m = re.match(r'^\s*0*(\d{3,6})\s*[-\s]', s) or re.search(r'\((\d{3,6})\)', s)
    return m.group(1) if m else None

excel_ctx = {'DATTO': {}, 'M365': {}}
for sec, rows in sections.items():
    for row in rows:
        label, action, comment, qty, _ = (list(row)+[None]*5)[:5]
        code = extract_code(label)
        if not code: continue
        excel_ctx[sec][code] = {
            'action': str(action or '').strip(),
            'comment': str(comment or '').strip(),
            'label': str(label).strip(),
        }

def datto_counts(code):
    sites = datto_by_code.get(code, [])
    s = sum(int(x.get('servers') or 0) for x in sites)
    p = sum(int(x.get('desktops') or 0) + int(x.get('laptops') or 0) for x in sites)
    return p, s

def rfc_lines_for(code, service):
    lines = []
    for ct in contrats_by_code.get(code, []):
        for line in ct.get('rfc_lines') or []:
            if line.get('service') == service:
                lines.append(line)
    return lines

def _strip_code_prefix(code, name):
    """'12534 - BONNEAU SAS - AGENCE LYON' → 'BONNEAU SAS - AGENCE LYON'"""
    if not name: return ''
    pat = rf'^\s*0*{re.escape(code)}\s*[-\s]\s*'
    return re.sub(pat, '', name).strip()

def client_label(code):
    ctx = excel_ctx['DATTO'].get(code) or excel_ctx['M365'].get(code)
    if ctx: return ctx['label']
    r = recon.get(code) or {}
    nom = r.get('name') or ''
    if not nom:
        # Fallback : chercher dans les sources vendeurs
        for src in ('datto','m365','sophos','sentinelone','veeam','vade'):
            for item in MSP.get('raw_sources', {}).get(src, []):
                ac = str(item.get('artis_code') or '').lstrip('0') or '0'
                if ac == code:
                    raw = item.get('name') or item.get('customer_name') or ''
                    nom = _strip_code_prefix(code, raw)
                    if nom: break
            if nom: break
    return f'{code} - {nom}' if nom else f'{code} - ?'

rows_out = []

# === DATTO ===
for code in DATTO_CODES:
    client = client_label(code)
    ctx = excel_ctx['DATTO'].get(code, {})
    excel_action  = ctx.get('action', '')
    excel_comment = ctx.get('comment', '')
    p, s = datto_counts(code)
    rfcs = rfc_lines_for(code, 'datto')

    if not rfcs:
        if p + s == 0:
            action_txt = '✅ OK — rien sur Artis ni sur DATTO'
        else:
            action_txt = f"⚠ {p} poste(s) + {s} serveur(s) actifs sur DATTO sans facturation Artis → à résilier (ou à facturer)"
        rows_out.append({
            'code': code, 'client': client, 'service': 'DATTO',
            'rfc': '(aucune RFC DATTO sur Artis)', 'qty': 0,
            'datto_s': s, 'datto_p': p,
            'action_factu': excel_action, 'commentaire_factu': excel_comment,
            'action_dfm': action_txt,
        })
        continue

    for i, rfc in enumerate(rfcs):
        rfc_lib = (rfc.get('rfc') or '').strip()
        q = int(float(rfc.get('qty') or 0))
        rl = rfc_lib.lower()
        is_serv = ('serveur' in rl or 'server' in rl) and 'poste' not in rl
        is_post = ('poste' in rl or 'workstation' in rl) and 'serveur' not in rl
        comments = []
        if is_serv:
            diff = s - q
            if diff == 0: comments.append(f'✅ Serveurs facturés OK ({q}={s})')
            elif diff > 0: comments.append(f'⚠ {diff} serveur(s) DATTO non facturé(s)')
            else: comments.append(f'⚠ Manque {-diff} serveur(s) sur DATTO (facturé {q}, déployé {s})')
            if p > 0 and not any('poste' in (o.get('rfc') or '').lower() for o in rfcs):
                comments.append(f'⚠ {p} poste(s) DATTO non facturé(s)')
        elif is_post:
            diff = p - q
            if diff == 0: comments.append(f'✅ Postes facturés OK ({q}={p})')
            elif diff > 0: comments.append(f'⚠ {diff} poste(s) DATTO non facturé(s)')
            else: comments.append(f'⚠ Manque {-diff} poste(s) sur DATTO (facturé {q}, déployé {p})')
            if s > 0 and not any(('serveur' in (o.get('rfc') or '').lower() or 'server' in (o.get('rfc') or '').lower()) for o in rfcs):
                comments.append(f'⚠ {s} serveur(s) DATTO non facturé(s)')
        else:
            total = p + s
            if total == q: comments.append(f'✅ Total OK ({q}={total})')
            elif total > q: comments.append(f'⚠ {total-q} machine(s) DATTO non facturée(s)')
            else: comments.append(f'⚠ Manque {q-total} machine(s) sur DATTO')
        rows_out.append({
            'code': code, 'client': client, 'service': 'DATTO',
            'rfc': rfc_lib, 'qty': q,
            'datto_s': s if i == 0 else '',
            'datto_p': p if i == 0 else '',
            'action_factu': excel_action if i == 0 else '',
            'commentaire_factu': excel_comment if i == 0 else '',
            'action_dfm': ' · '.join(comments),
        })

# === M365 ===
def m365_summary(code):
    subs = m365_by_code.get(code, [])
    if not subs: return '(rien sur TDSynnex)', 0
    return '; '.join(f"{s.get('subscription','?')} x{s.get('licenses',0)}" for s in subs), sum(int(s.get('licenses',0) or 0) for s in subs)

for code in M365_CODES:
    client = client_label(code)
    ctx = excel_ctx['M365'].get(code, {})
    excel_action  = ctx.get('action', '')
    excel_comment = ctx.get('comment', '')
    rfcs = rfc_lines_for(code, 'm365')
    tdss_summary, total_lic = m365_summary(code)

    if not rfcs:
        if total_lic == 0:
            action_txt = '✅ OK — rien sur Artis ni sur TDSynnex'
        else:
            action_txt = f"⚠ {total_lic} lic actives sur TDSynnex sans facturation Artis M365"
        rows_out.append({
            'code': code, 'client': client, 'service': 'M365',
            'rfc': '(aucune RFC M365 sur Artis)', 'qty': 0,
            'datto_s': '', 'datto_p': tdss_summary[:120],
            'action_factu': excel_action, 'commentaire_factu': excel_comment,
            'action_dfm': action_txt,
        })
        continue

    total_qty_rfc = sum(float(r.get('qty') or 0) for r in rfcs)
    for i, rfc in enumerate(rfcs):
        q = int(float(rfc.get('qty') or 0))
        rfc_lib = (rfc.get('rfc') or '').strip()
        if i == 0:
            diff = total_lic - total_qty_rfc
            if abs(diff) < 0.5:
                action_txt = f'✅ Cohérent ({int(total_qty_rfc)} facturé = {total_lic} déployé)'
            elif diff > 0:
                action_txt = f'⚠ {int(diff)} lic TDSynnex non facturée(s) ({int(total_qty_rfc)} facturé / {total_lic} déployé)'
            else:
                action_txt = f'⚠ Manque {int(-diff)} lic sur TDSynnex (facturé {int(total_qty_rfc)} / déployé {total_lic})'
        else:
            action_txt = ''
        rows_out.append({
            'code': code, 'client': client, 'service': 'M365',
            'rfc': rfc_lib, 'qty': q,
            'datto_s': '', 'datto_p': tdss_summary[:120] if i == 0 else '',
            'action_factu': excel_action if i == 0 else '',
            'commentaire_factu': excel_comment if i == 0 else '',
            'action_dfm': action_txt,
        })

# Write
wb = openpyxl.Workbook()
ws = wb.active
ws.title = 'Audit'
ws.freeze_panes = 'A2'
HEADER_FILL = PatternFill('solid', fgColor='1F4E78')
HEADER_FONT = Font(color='FFFFFF', bold=True)
WARN_FILL   = PatternFill('solid', fgColor='FFE699')
OK_FILL     = PatternFill('solid', fgColor='C6EFCE')

headers = ['Code', 'Client', 'Service', 'RFC Artis facturée', 'Qté Artis',
           'DATTO serveurs (ou lic TDSynnex)', 'DATTO postes (ou détail SKU)',
           'Action factu (col B)', 'Commentaire factu (col C)', 'Action DFM (calculée)']
for i, h in enumerate(headers, 1):
    c = ws.cell(1, i, h)
    c.fill = HEADER_FILL; c.font = HEADER_FONT
    c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
for r in rows_out:
    ws.append([r['code'], r['client'], r['service'], r['rfc'], r['qty'],
               r['datto_s'], r['datto_p'], r['action_factu'], r['commentaire_factu'], r['action_dfm']])
    n = ws.max_row
    if r['action_dfm'].startswith('⚠'): ws.cell(n, 10).fill = WARN_FILL
    elif r['action_dfm'].startswith('✅'): ws.cell(n, 10).fill = OK_FILL

widths = {'A':7, 'B':40, 'C':8, 'D':50, 'E':10, 'F':14, 'G':55, 'H':18, 'I':45, 'J':55}
for col, w in widths.items(): ws.column_dimensions[col].width = w
for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
    for cell in row: cell.alignment = Alignment(vertical='top', wrap_text=True)
ws.auto_filter.ref = ws.dimensions

out = Path.cwd() / 'Audit_Console_Avril_2026.xlsx'
wb.save(out)
print(f'OK : {out.name} — {len(rows_out)} lignes (DATTO={len(DATTO_CODES)} clients, M365={len(M365_CODES)} clients)')
