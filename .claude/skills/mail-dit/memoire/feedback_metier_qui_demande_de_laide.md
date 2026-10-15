---
name: feedback-metier-qui-demande-de-laide
description: Quand un autre service DFM ecrit au support, le mail est POUR nous — ne jamais le lui renvoyer
metadata:
  type: feedback
---

L'adresse d'un autre metier (CAT/impression, referents telecom) dans les
destinataires ne veut dire « c'est pour eux » que si **ce metier n'est pas
l'expediteur**. Quand c'est lui qui nous ecrit, il demande de l'aide : le mail
est pour nous, et le renvoyer fait tourner le dossier en rond pendant que le
client attend.

**Why:** dossier BSA (4048), 2026-09-24. Le CAT ecrit au support pour un poste
que son technicien Serge BARCELO n'arrive pas a prendre en main (pas de
TeamViewer, ecran noir en assistance rapide). Le support renvoie : « ce mail
releve de votre service (destinataire cat@dfm.fr) ». Le CAT renvoie au support.
Johanne : « Serge me demande de transferer au support, et le support dit que cela
releve de notre service. En attendant, le client attend toujours d'etre depanne. »
Elliot : « a un moment l'impression demande de l'aide au support info et toi tu
continues de leur transferer ».

**How to apply:** deux verrous dans `mail_dit.py`.
1. `metier_du_destinataire(dest, expediteur=...)` ignore un metier dont
   l'expediteur fait partie (table `REFERENTS`, ou `sbarcelo@dfm.fr` a ete
   ajoute comme hotline impression).
2. `PHRASE_RELAIS` — si notre propre phrase de transfert figure deja dans le
   fil, le mail a deja ete renvoye une fois : ce qui revient est une reponse,
   pas une erreur d'aiguillage. On le garde.
Voir [[feedback-metier-lire-le-corps]].
