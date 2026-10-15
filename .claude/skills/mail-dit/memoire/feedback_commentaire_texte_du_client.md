---
name: feedback_commentaire_texte_du_client
description: "Le commentaire d'une DIT reprend le mail du client mot pour mot — jamais une reformulation, meme meilleure"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 839132cb-cb04-4d5b-8f11-b0013c373e0f
  modified: 2026-09-29T16:11:58.913Z
---

Elliot, le 2026-09-29 sur la DIT **100175733** : « stp tu ne REFORMULE PAS LA
DEMANDE a ta sauce tu reprend le mail du client... **tout en gardant
l'historique, et la modif du formattage** ».

**Why** : le commentaire est la piece au dossier. Reformule, il devient mon
interpretation de la demande, plus la demande. Le technicien ne peut plus
verifier ce que le client a reellement ecrit, et une nuance perdue a la
reecriture ne se recupere jamais. C'est aussi ce qui fait qu'un ticket relu six
mois plus tard reste opposable.

**Ce que j'avais ecrit** (mauvais) :
> Creer une adresse e-mail Paris FC pour Abderrahmane BOULEGHLEM.
> Le client demande que les codes lui soient ensuite envoyes a l'adresse
> personnelle blm.professionnel@gmail.com, qui est une adresse grand public
> exterieure au domaine du client.

**Ce qu'il fallait** : la ligne d'objet du mail, puis le texte du client tel
quel.

**How to apply** — la forme attendue :
1. **Ligne 1** : l'objet **du mail du client**, pas un titre que j'invente.
2. Ligne vide, puis le **corps du mail mot pour mot**, formules de politesse
   comprises.
3. **Signature exclue** (nom, telephone, adresse, mentions legales) — cf.
   [[feedback_commentaire_dit_sans_avis]].
4. **Historique conserve** quand le mail en cite un : bandeau
   `E.entete_ajout(...)` pour un ajout du client, separateur d'historique pour
   les messages precedents du fil.
5. **Normalisation ISO** systematique : `E._stockable(texte)`. Artis stocke en
   ISO-8859-15 et supprime en silence ce qu'il ne sait pas encoder.
6. **Aucun avis, aucune mise en garde de ma part** dans le commentaire. Le
   sensible se dit a Elliot dans le rapport, pas dans le ticket.

Recuperer le texte : le corps HTML via Graph, puis strip des balises et
`html.unescape`. Ne pas se contenter du `summary` de la recherche — il est
tronque (piege du 2026-09-29 sur le mail APL).

Vaut aussi pour un mail en anglais : le reprendre tel quel plutot que de le
traduire.

**Le RAPPORT aussi.** Elliot, le 2026-09-29 : « **tire pas de conclusion**, fais
juste la creation des DIT ». Le rapport de passage dit ce qui a ete cree, range,
laisse — et pourquoi, en une ligne. Pas de synthese, pas de tendance reperee
(« troisieme cas de la journee », « le tri fonctionne, c'est l'aval qui coince »,
« ce dossier merite ton attention »), pas de recommandation non demandee. Les
faits appelent leurs conclusions tout seuls, et c'est Elliot qui les tire.
Signaler reste attendu pour ce que les garde-fous imposent : mail sensible,
creation de contact, symptome de repli, doublon annule.

Voir [[project_mail_to_dit_artis]] et [[feedback_joindre_les_pieces]].
