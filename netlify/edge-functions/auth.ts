/**
 * Protection par mot de passe du site (authentification HTTP Basic).
 *
 * Le plan gratuit de Netlify n'inclut pas la protection par mot de passe
 * integree ; cette fonction edge la fournit. Le mot de passe est lu dans la
 * variable d'environnement SITE_PASSWORD definie dans l'interface Netlify :
 * il n'apparait jamais dans le depot.
 *
 * Si SITE_PASSWORD n'est pas defini, le site reste accessible : ce choix
 * evite de rendre le site inaccessible par simple oubli de configuration,
 * mais il faut donc bien penser a definir la variable.
 */
export default async (request: Request): Promise<Response | undefined> => {
  const attendu = Deno.env.get("SITE_PASSWORD");
  if (!attendu) {
    // Aucun mot de passe configure : la fonction se retire et laisse passer.
    return undefined;
  }

  const entete = request.headers.get("authorization") || "";
  if (entete.toLowerCase().startsWith("basic ")) {
    try {
      const decode = atob(entete.slice(6));
      const separateur = decode.indexOf(":");
      const fourni = separateur >= 0 ? decode.slice(separateur + 1) : "";
      if (comparaisonConstante(fourni, attendu)) {
        return undefined; // acces autorise
      }
    } catch {
      // En-tete mal formee : traitee comme une absence d'identifiants.
    }
  }

  return new Response(
    "Accès protégé — outil personnel.\n\nSaisissez le mot de passe (le nom d'utilisateur est libre).",
    {
      status: 401,
      headers: {
        "WWW-Authenticate": 'Basic realm="Investassist", charset="UTF-8"',
        "Content-Type": "text/plain; charset=utf-8",
        "Cache-Control": "no-store",
      },
    },
  );
};

/**
 * Comparaison a duree constante : evite de laisser deviner le mot de passe
 * caractere par caractere en mesurant le temps de reponse.
 */
function comparaisonConstante(a: string, b: string): boolean {
  const encodeur = new TextEncoder();
  const octetsA = encodeur.encode(a);
  const octetsB = encodeur.encode(b);
  if (octetsA.length !== octetsB.length) {
    return false;
  }
  let difference = 0;
  for (let i = 0; i < octetsA.length; i++) {
    difference |= octetsA[i] ^ octetsB[i];
  }
  return difference === 0;
}

export const config = {
  // La page d'erreur 404 de Netlify et les fichiers internes restent exclus.
  path: "/*",
  excludedPath: ["/.netlify/*"],
};
