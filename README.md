# Observatoire — environnement et libertés civiles

Veille francophone et internationale, publiée deux fois par semaine sous forme de page Web et de flux RSS.

## Ce que fait le projet
- Interroge des flux RSS/Atom configurés dans `feeds.json`.
- Filtre les articles avec des mots-clés liés à l'environnement et aux libertés civiles.
- Équilibre la sélection entre les deux thèmes (jusqu'à 15 articles).
- Produit `docs/index.html` et `docs/feed.xml`.
- Fonctionne sans clé d'IA. Avec `OPENAI_API_KEY`, il demande des résumés et une analyse préliminaire à l'API OpenAI. Les frais API sont distincts d'un abonnement ChatGPT.

## Mise en place sur GitHub

1. Crée un compte sur https://github.com si tu n'en as pas.
2. Crée un dépôt **public** nommé `observatoire-environnement-libertes` (public est nécessaire pour utiliser GitHub Pages gratuitement dans la configuration la plus simple).
3. Sur la page du dépôt, choisis **Add file → Upload files** et téléverse le contenu de cette archive décompressée (pas le dossier parent).
4. Va dans **Settings → Pages**. Dans *Build and deployment*, choisis **GitHub Actions**.
5. Va dans **Actions** et autorise les workflows si GitHub le demande.
6. Ouvre **Actions → Publier la veille** puis clique **Run workflow** pour faire un premier test.
7. Après la première exécution réussie, la page et le flux RSS seront publiés à :
   - `https://TON-NOM.github.io/observatoire-environnement-libertes/`
   - `https://TON-NOM.github.io/observatoire-environnement-libertes/feed.xml`

Remplace `TON-NOM` par ton identifiant GitHub. Le dépôt doit garder le nom indiqué, sauf si tu ajustes les URL.

## Horaire
Le workflow est prévu les mardis et vendredis à 8 h (heure de Montréal). GitHub prend en charge le fuseau `America/Toronto` dans les tâches planifiées. Tu peux modifier les jours et l'heure dans `.github/workflows/publish.yml`.

## Ajouter une clé d'IA (facultatif)
1. Crée une clé API dans la plateforme développeur OpenAI : https://platform.openai.com/api-keys
2. Dans GitHub : **Settings → Secrets and variables → Actions → New repository secret**.
3. Nom : `OPENAI_API_KEY`; valeur : ta clé.
4. Ajoute aussi `OPENAI_MODEL` si tu souhaites choisir un modèle compatible avec l'API Responses. Le défaut du projet est `gpt-6-luna`.
5. Ne mets jamais la clé directement dans un fichier du dépôt.

L'IA n'est pas activée tant que le secret n'est pas ajouté. Vérifie les tarifs API avant de l'activer.

## Personnaliser les sources
Modifie `feeds.json`. Les flux officiels et médias sont identifiés par leur nom. Les flux Google Actualités sont des recherches thématiques; les articles restent hébergés chez leurs éditeurs.

Sources initiales incluses :
- Fil de presse du gouvernement du Québec.
- Fil Atom du Centre des nouvelles du gouvernement du Canada (français).
- Fil du ministère fédéral de la Justice.
- La Presse — environnement.
- Recherches thématiques Google Actualités pour compléter la veille sur la société civile, la surveillance et l'environnement.

Les flux disponibles peuvent changer. Une source absente ou momentanément inaccessible ne signifie pas qu'il n'y a aucune nouvelle.

## Limites éditoriales
- Il s'agit d'une veille documentaire, pas d'un avis juridique.
- Les résumés automatiques peuvent contenir des erreurs; ouvre toujours la source originale avant de citer une affirmation.
- Le classement par mots-clés est imparfait. Ajuste `KEYWORDS` dans `build.py` selon tes intérêts.
- GitHub Pages rend le site public : n'y ajoute aucune information personnelle ou confidentielle.

## Dépannage
- Si le site ne s'affiche pas, vérifie **Settings → Pages** et le dernier résultat dans **Actions**.
- Si le flux RSS est vide, vérifie les exécutions Actions et les URLs dans `feeds.json`.
- Pour déclencher une mise à jour manuelle : **Actions → Publier la veille → Run workflow**.
