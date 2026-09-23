# GitHub and launch

## Run locally

From the repository root:

```powershell
npm run dev
```

Open the local URL printed by `serve`.

## Publish on GitHub Pages

1. Create an empty GitHub repository.
2. Push this repository to its `main` branch.
3. In GitHub, open **Settings > Pages** and set **Source** to **GitHub Actions**.
4. Push to `main` or run **Deploy Syllabus Core** from the Actions tab.
5. GitHub will publish the static app at the Pages URL shown in the workflow summary.

The uploaded artifact is the static entrypoint at `index.html`. The blueprint, gold Theme 1 data, Supabase migration, tests, and generation engine remain available in their existing folders.

## Important limitation

The preview's upload, generation, team, and review actions are browser-local demonstration behavior. They do not persist to Supabase or send email until the Supabase client and authenticated server actions are connected. GitHub Pages can host the preview, but it cannot provide those backend services by itself.
