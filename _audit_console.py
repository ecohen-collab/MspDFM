"""Audit détaillé : Etat console AVRIL 2026.xlsx vs Artis billing + DATTO/M365 inventaires."""
import json, os, re
from pathlib import Path
import openpyxl

TEMP = os.environ.get('TEMP') or '/tmp'
MSP = json.loads(Path(TEMP, 'msp_data.json').read_text(encoding='utf-8'))

# Indexes
recon = {r['code']: r for r in MSP.get('reconciliation', []) if r.get('code')}
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

def extract_code(label):
    if not label: return None
    s = str(label)
    m = re.match(r'^\s*0*(\d{3,6})\s*[-\s]', s) or re.search(r'\((\d{3,6})\)', s)
    return m.group(1) if m else None

# Excel
wb = openpyxl.load_workbook(Path.cwd() / 'Etat console AVRIL 2026.xlsx', data_only=True)
ws = wb['avril']
sections, current = {}, None
for row in ws.iter_rows(values_only=True):
    if all(c is None for c in row): continue
    c0 = row[0]
    if c0 and not row[1] and not row[2] and isinstance(c0, str) and c0.strip().upper() in ('DATTO','M365'):
        current = c0.strip().upper(); sections[current] = []; continue
    if current: sections[current].append(row[:5])

def datto_counts(code):
    sites = datto_by_code.get(code, [])
    s = d = l = 0
    for site in sites:
        s += int(site.get('servers') or 0)
        d += int(site.get('desktops') or 0)
        l += int(site.get('laptops') or 0)
    return s, d+l   # servers, postes

def artis_billing(code):
    r = recon.get(code) or {}
    a = r.get('artis') or {}
    return {k: int(a.get(k) or 0) for k in ('datto','m365','sophos','veeam_tb','veeam365','vade','soc')}

def artis_name(code):
    r = recon.get(code) or {}
    return r.get('name') or ''

# === DATTO ===
poste_only, serv_only, mettre_en_place, pas_infog, autres = [], [], [], [], []
for row in sections.get('DATTO', []):
    label, action, comment, qty, _ = (list(row)+[None]*5)[:5]
    code = extract_code(label)
    if not code: continue
    a = str(action or '').lower()
    c = str(comment or '').lower().strip()
    s_d, p_d = datto_counts(code)
    bill = artis_billing(code)
    rec = (code, str(label).strip(), str(comment or '').strip(), int(qty) if isinstance(qty,(int,float)) else None,
           s_d, p_d, bill, artis_name(code))
    if 'à mettre en place' in a or 'a mettre en place' in a:
        mettre_en_place.append(rec)
    elif 'poste uniquement' in c or 'postes uniquement' in c:
        poste_only.append(rec)
    elif 'serveur uniquement' in c or 'server uniquement' in c or 'servers uniquement' in c:
        serv_only.append(rec)
    elif "pas d'infogérance" in c or "pas d'infogerance" in c:
        pas_infog.append(rec)
    else:
        autres.append(rec)

print('# DATTO\n')
print('## Postes uniquement sur Artis (Artis billed datto > 0, console DATTO doit montrer postes ET serveurs ?)\n')
print('| Code | Client | Artis datto qty | DATTO postes | DATTO serveurs | Cohérent ? |')
print('|---|---|---:|---:|---:|---|')
for code,label,_,_,s,p,bill,_ in poste_only:
    coh = '✅ postes seuls' if s == 0 else f'⚠️ {s} serveurs présents sur DATTO !'
    print(f'| {code} | {label} | {bill["datto"]} | {p} | {s} | {coh} |')

print('\n## Serveurs uniquement sur Artis (idem côté serveur)\n')
print('| Code | Client | Artis datto qty | DATTO postes | DATTO serveurs | Cohérent ? |')
print('|---|---|---:|---:|---:|---|')
for code,label,_,_,s,p,bill,_ in serv_only:
    coh = '✅ serveurs seuls' if p == 0 else f'⚠️ {p} postes présents sur DATTO !'
    print(f'| {code} | {label} | {bill["datto"]} | {p} | {s} | {coh} |')

print('\n## À mettre en place (Artis dit qu\'on a vendu, DATTO devrait être vide ou incomplet)\n')
print('| Code | Client | Commentaire facturation | Artis datto facturé | DATTO postes | DATTO serveurs |')
print('|---|---|---|---:|---:|---:|')
for code,label,comment,_,s,p,bill,_ in mettre_en_place:
    print(f'| {code} | {label} | {comment} | {bill["datto"]} | {p} | {s} |')

print('\n## "Pas d\'infogérance" (Artis ne devrait rien facturer en infogérance ; DATTO est actif = anomalie)\n')
print('| Code | Client | Artis datto | autres services Artis (m365/sophos/veeam/vade) | DATTO postes | DATTO serveurs |')
print('|---|---|---:|---|---:|---:|')
for code,label,_,_,s,p,bill,_ in pas_infog:
    others = ', '.join(f'{k}={v}' for k,v in bill.items() if v > 0 and k != 'datto') or '—'
    print(f'| {code} | {label} | {bill["datto"]} | {others} | {p} | {s} |')

# === M365 ===
m365_resilier, m365_mettre, m365_voir, m365_autres = [], [], [], []
for row in sections.get('M365', []):
    label, action, comment, qty, _ = (list(row)+[None]*5)[:5]
    code = extract_code(label)
    if not code: continue
    a = str(action or '').lower()
    subs = m365_by_code.get(code, [])
    rec = (code, str(label).strip(), str(comment or '').strip(), subs, artis_billing(code))
    if 'à résilier' in a or 'a resilier' in a:
        m365_resilier.append(rec)
    elif 'à mettre en place' in a or 'a mettre en place' in a:
        m365_mettre.append(rec)
    elif 'à voir' in a or 'a voir' in a:
        m365_voir.append(rec)
    else:
        m365_autres.append(rec)

print('\n\n# M365\n')
print('## À résilier — détail des subscriptions TDSynnex actives\n')
print('| Code | Client | SKU / Subscription | Licences | Status | End date | Commentaire facturation |')
print('|---|---|---|---:|---|---|---|')
for code,label,comment,subs,bill in m365_resilier:
    if not subs:
        print(f'| {code} | {label} | (aucune sub active sur TDSynnex) | 0 | — | — | {comment} |')
    else:
        for s in subs:
            print(f'| {code} | {label} | {s.get("subscription","")} | {s.get("licenses",0)} | {s.get("status","")} | {s.get("end_date","")} | {comment if subs.index(s)==0 else ""} |')

print('\n## À mettre en place — vendu sur Artis, TDSynnex doit être vide ou partiel\n')
print('| Code | Client | Commentaire facturation (vendu) | Artis m365 qty | TDSynnex subs (SKU x lic) |')
print('|---|---|---|---:|---|')
for code,label,comment,subs,bill in m365_mettre:
    tdss = '; '.join(f'{s.get("subscription","?")} x{s.get("licenses",0)}' for s in subs) or '(rien)'
    print(f'| {code} | {label} | {comment} | {bill["m365"]} | {tdss} |')

print('\n## À voir / autres\n')
print('| Code | Client | Action | Commentaire | Artis m365 | TDSynnex subs |')
print('|---|---|---|---|---:|---|')
for code,label,comment,subs,bill in m365_voir + m365_autres:
    tdss = '; '.join(f'{s.get("subscription","?")} x{s.get("licenses",0)}' for s in subs) or '(rien)'
    print(f'| {code} | {label} | à voir | {comment} | {bill["m365"]} | {tdss} |')
