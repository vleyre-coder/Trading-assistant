/* =====================================================================
   Investassist — interface du site statique.
   Aucune dependance externe, aucun appel reseau sortant : la page lit
   uniquement les fichiers JSON produits par l'analyse et publies a cote.
   ===================================================================== */
(() => {
  "use strict";

  const CLE_WATCHLIST = "investassist.watchlist";
  const CLE_THEME = "investassist.theme";
  const THEMES = ["auto", "light", "dark"];
  const LIBELLE_THEME = { auto: "automatique", light: "clair", dark: "sombre" };
  const etat = {
    // Renseigne lorsque la page est servie par l'application locale : elle
    // expose alors une API que la version hors ligne n'a pas.
    appli: null,
    jeton: null,
    watchlist: [],
    analyse: null,
    alertes: null,
    donnees: null,
    historique: null,
    vue: "classement",
    selection: null,
    tri: { colonne: "rank", ordre: 1 },
    filtres: { recherche: "", zone: "", secteur: "" },
  };

  /* ------------------------------------------------------------ outils */
  const $ = (selecteur) => document.querySelector(selecteur);
  const creer = (balise, classe, texte) => {
    const noeud = document.createElement(balise);
    if (classe) noeud.className = classe;
    if (texte !== undefined) noeud.textContent = texte;
    return noeud;
  };

  const nombre = (valeur, decimales = 1) =>
    valeur === null || valeur === undefined || Number.isNaN(valeur)
      ? "n/d"
      : Number(valeur).toLocaleString("fr-FR", {
          minimumFractionDigits: decimales,
          maximumFractionDigits: decimales,
        });

  /** Mise en forme d'un critere selon son unite. */
  function valeurCritere(valeur, unite) {
    if (valeur === null || valeur === undefined) return "n/d";
    if (unite === "percent") return `${nombre(valeur * 100, 1)} %`;
    if (unite === "pp") return `${valeur >= 0 ? "+" : ""}${nombre(valeur * 100, 1)} pts`;
    return nombre(valeur, 2);
  }

  /** « il y a 3 h », « hier », « le 12 août » : repere plus parlant qu'une
      date brute pour juger de la fraicheur d'une analyse. */
  function delaiLisible(iso) {
    const quand = new Date(iso);
    if (Number.isNaN(quand.getTime())) return iso;
    const minutes = Math.round((Date.now() - quand.getTime()) / 60000);
    if (minutes < 2) return "à l'instant";
    if (minutes < 60) return `il y a ${minutes} min`;
    const heures = Math.round(minutes / 60);
    if (heures < 24) return `il y a ${heures} h`;
    const jours = Math.round(heures / 24);
    if (jours === 1) return "hier";
    if (jours < 7) return `il y a ${jours} jours`;
    return quand.toLocaleDateString("fr-FR", { day: "numeric", month: "long" });
  }

  function majHorodatage(iso) {
    const jeton = $("#horodatage");
    jeton.innerHTML = "";
    jeton.append(creer("span", "point"), creer("span", null, `Analyse ${delaiLisible(iso)}`));
    jeton.title = `Dernière analyse : ${dateLisible(iso)}`;
  }

  function themeActuel() {
    try {
      return localStorage.getItem(CLE_THEME) || "auto";
    } catch (erreur) {
      return "auto";
    }
  }

  function appliquerTheme(theme) {
    if (theme === "auto") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = theme;
    const bouton = $("#bascule-theme");
    if (bouton) bouton.title = `Thème : ${LIBELLE_THEME[theme]}`;
    try {
      localStorage.setItem(CLE_THEME, theme);
    } catch (erreur) {
      /* stockage indisponible : le theme vaut pour la session seulement */
    }
  }

  const dateLisible = (iso) => {
    const d = new Date(iso);
    return Number.isNaN(d.getTime())
      ? iso
      : d.toLocaleString("fr-FR", { dateStyle: "long", timeStyle: "short" });
  };

  function lireWatchlist() {
    try {
      const brut = localStorage.getItem(CLE_WATCHLIST);
      return brut ? JSON.parse(brut) : [];
    } catch (erreur) {
      return [];
    }
  }

  function ecrireWatchlist(liste) {
    try {
      localStorage.setItem(CLE_WATCHLIST, JSON.stringify(liste));
    } catch (erreur) {
      /* Navigation privee ou stockage refuse : la watchlist reste en memoire
         pour la session, sans casser le reste de la page. */
    }
  }

  /** En mode application la watchlist vit dans la base locale (elle suit donc
      le dossier copie d'un ordinateur a l'autre) ; hors ligne elle reste dans
      le navigateur. */
  const listeSuivie = () => (etat.appli ? etat.watchlist : lireWatchlist());

  const estSuivi = (ticker) => listeSuivie().includes(ticker);

  async function basculerWatchlist(ticker) {
    if (etat.appli) {
      const reponse = etat.watchlist.includes(ticker)
        ? await api(`/api/watchlist/${encodeURIComponent(ticker)}`, { methode: "DELETE" })
        : await api("/api/watchlist", { methode: "POST", corps: { ticker } });
      etat.watchlist = reponse.titres.map((t) => t.ticker);
      return;
    }
    const liste = lireWatchlist();
    const index = liste.indexOf(ticker);
    if (index >= 0) liste.splice(index, 1);
    else liste.push(ticker);
    ecrireWatchlist(liste);
  }

  /* ----------------------------------------------------------- client API */
  function lireJeton() {
    const parametres = new URLSearchParams(window.location.search);
    const depuisUrl = parametres.get("jeton");
    if (depuisUrl) {
      try {
        sessionStorage.setItem("investassist.jeton", depuisUrl);
      } catch (erreur) {
        /* stockage refusé : le jeton vaut pour cette page seulement */
      }
      // Le jeton est retiré de la barre d'adresse : il ne doit pas finir
      // dans un favori ni dans l'historique du navigateur.
      window.history.replaceState({}, "", window.location.pathname);
      return depuisUrl;
    }
    try {
      return sessionStorage.getItem("investassist.jeton");
    } catch (erreur) {
      return null;
    }
  }

  async function api(chemin, options = {}) {
    const reponse = await fetch(chemin, {
      method: options.methode || "GET",
      headers: {
        "X-Jeton": etat.jeton || "",
        ...(options.corps ? { "Content-Type": "application/json" } : {}),
      },
      body: options.corps ? JSON.stringify(options.corps) : undefined,
      cache: "no-store",
    });
    const charge = await reponse.json().catch(() => ({}));
    if (!reponse.ok) {
      throw new Error(charge.erreur || `HTTP ${reponse.status}`);
    }
    return charge;
  }

  /* ------------------------------------------------------- chargement */
  async function charger() {
    etat.jeton = lireJeton();
    // Detection du mode : si l'API repond, l'interface active ses fonctions
    // interactives ; sinon elle reste en lecture seule, comme hors ligne.
    try {
      etat.appli = await api("/api/etat");
      etat.watchlist = (await api("/api/watchlist")).titres.map((t) => t.ticker);
      activerOngletAlertes();
    } catch (erreur) {
      etat.appli = null;
    }

    try {
      const [classement, historique] = await Promise.all([
        fetch("data/ranking.json", { cache: "no-cache" }).then((r) => {
          if (!r.ok) throw new Error(`ranking.json : HTTP ${r.status}`);
          return r.json();
        }),
        fetch("data/history.json", { cache: "no-cache" })
          .then((r) => (r.ok ? r.json() : null))
          .catch(() => null),
      ]);
      etat.donnees = classement;
      etat.historique = historique;
      appliquerAvertissements(classement.disclaimer || {});
      majHorodatage(classement.generated_at);
      rendre();
    } catch (erreur) {
      $("#contenu").innerHTML = "";
      const carte = creer("section", "carte");
      carte.append(creer("h2", null, "Données indisponibles"));
      carte.append(
        creer(
          "p",
          "note",
          "Le fichier d'analyse n'a pas pu être chargé : " +
            erreur.message +
            ". Si le site vient d'être créé, la première analyse planifiée ne s'est " +
            "peut-être pas encore exécutée."
        )
      );
      $("#contenu").append(carte);
    }
  }

  function appliquerAvertissements(avertissement) {
    if (avertissement.main) {
      $("#avertissement-principal").textContent = avertissement.main;
      $("#pied-avertissement").textContent = `⚠️ ${avertissement.main}`;
    }
    $("#avertissement-est").textContent = (avertissement.what_this_is || "").replace(/\*\*/g, "");
    $("#avertissement-nest-pas").textContent = avertissement.what_this_is_not || "";
    $("#avertissement-donnees").textContent = avertissement.data_limits || "";
  }

  /* ---------------------------------------------------------- rendu */
  function rendre() {
    document.querySelectorAll("nav.onglets button").forEach((bouton) => {
      bouton.setAttribute("aria-selected", String(bouton.dataset.vue === etat.vue));
    });
    const contenu = $("#contenu");
    contenu.innerHTML = "";
    const vues = {
      classement: vueClassement,
      alertes: vueAlertes,
      watchlist: vueWatchlist,
      exclus: vueExclus,
      methodologie: vueMethodologie,
    };
    (vues[etat.vue] || vueClassement)(contenu);
  }

  /* ---------------------------------------------------- barre d'analyse */
  const LIBELLES_ALERTES = {
    price_above: "Cours au-dessus d'un seuil",
    price_below: "Cours au-dessous d'un seuil",
    score_change: "Variation du score composite",
    earnings_published: "Nouvelle publication de résultats",
    top_n_entry: "Entrée dans le top N",
    top_n_exit: "Sortie du top N",
  };

  function activerOngletAlertes() {
    if (document.querySelector('nav.onglets button[data-vue="alertes"]')) return;
    const nav = document.querySelector("nav.onglets");
    const bouton = creer("button", null, "Alertes");
    bouton.setAttribute("role", "tab");
    bouton.dataset.vue = "alertes";
    bouton.setAttribute("aria-selected", "false");
    bouton.addEventListener("click", () => {
      etat.vue = "alertes";
      rendre();
    });
    // Placee avant l'onglet Méthodologie, qui reste le dernier.
    const methodologie = document.querySelector('nav.onglets button[data-vue="methodologie"]');
    nav.insertBefore(bouton, methodologie);
  }

  /**
   * Commande de relance : selection des univers, cache, progression.
   * Une analyse complete demande plusieurs minutes ; l'avancement est donc
   * affiche titre par titre, sinon l'utilisateur croit l'outil bloque.
   */
  function barreAnalyse() {
    const bloc = creer("div", "panneau");
    const analyse = etat.analyse || (etat.appli && etat.appli.analyse) || {};

    if (analyse.en_cours) {
      const total = analyse.total || 0;
      const fait = analyse.fait || 0;
      bloc.append(creer("h3", null, "Analyse en cours"));
      const piste = creer("div", "barre-piste");
      const remplissage = creer("span");
      remplissage.style.width = total ? `${Math.round((fait / total) * 100)}%` : "4%";
      piste.append(remplissage);
      bloc.append(piste);
      bloc.append(
        creer(
          "p",
          "note",
          total
            ? `${fait} / ${total} titres — ${analyse.ticker || "…"}`
            : "Préparation…"
        )
      );
      bloc.append(
        creer(
          "p",
          "note-discrete",
          "Vous pouvez continuer à consulter le classement précédent pendant le calcul."
        )
      );
      return bloc;
    }

    bloc.append(creer("h3", null, "Relancer l'analyse"));

    const univers = creer("div", "filtres");
    (etat.appli.univers || []).forEach((bloc_univers) => {
      const etiquette = creer("label", "etiquette");
      const case_a_cocher = creer("input");
      case_a_cocher.type = "checkbox";
      case_a_cocher.value = bloc_univers.cle;
      case_a_cocher.checked = (etat.universChoisis || etat.appli.univers_par_defaut || []).includes(
        bloc_univers.cle
      );
      case_a_cocher.addEventListener("change", () => {
        const choisis = new Set(
          etat.universChoisis || etat.appli.univers_par_defaut || []
        );
        if (case_a_cocher.checked) choisis.add(bloc_univers.cle);
        else choisis.delete(bloc_univers.cle);
        etat.universChoisis = [...choisis];
        rendre();
      });
      etiquette.append(case_a_cocher, creer("span", null, `${bloc_univers.libelle} (${bloc_univers.nombre})`));
      univers.append(etiquette);
    });
    bloc.append(univers);

    const options = creer("div", "filtres");
    const etiquetteCache = creer("label", "etiquette");
    const cache = creer("input");
    cache.type = "checkbox";
    cache.checked = etat.utiliserCache !== false;
    cache.addEventListener("change", () => {
      etat.utiliserCache = cache.checked;
    });
    etiquetteCache.append(cache, creer("span", null, "Utiliser les données en cache (12 h)"));
    options.append(etiquetteCache);
    bloc.append(options);

    const choisis = etat.universChoisis || etat.appli.univers_par_defaut || [];
    const nombre = (etat.appli.univers || [])
      .filter((u) => choisis.includes(u.cle))
      .reduce((somme, u) => somme + u.nombre, 0);

    const lancer = creer("button", "bouton principal", "▶ Lancer l'analyse maintenant");
    lancer.disabled = nombre === 0;
    lancer.addEventListener("click", async () => {
      lancer.disabled = true;
      try {
        await api("/api/analyse", {
          methode: "POST",
          corps: { univers: choisis, cache: etat.utiliserCache !== false },
        });
        suivreAnalyse();
      } catch (erreur) {
        alert(`Analyse impossible : ${erreur.message}`);
        lancer.disabled = false;
      }
    });
    bloc.append(lancer);
    bloc.append(
      creer(
        "p",
        "note-discrete",
        nombre
          ? `${nombre} titres — environ ${Math.max(1, Math.round((nombre * 3.5) / 60))} min ` +
            "sans cache, beaucoup moins avec."
          : "Sélectionnez au moins un univers."
      )
    );
    return bloc;
  }

  /** Interroge l'avancement puis recharge les donnees a la fin. */
  function suivreAnalyse() {
    if (etat.suivi) clearInterval(etat.suivi);
    etat.suivi = setInterval(async () => {
      try {
        const reponse = await api("/api/etat");
        etat.appli = reponse;
        etat.analyse = reponse.analyse;
        if (!reponse.analyse.en_cours) {
          clearInterval(etat.suivi);
          etat.suivi = null;
          await charger();
          return;
        }
        rendre();
      } catch (erreur) {
        clearInterval(etat.suivi);
        etat.suivi = null;
      }
    }, 2000);
    rendre();
  }

  /* ------------------------------------------------------- classement */
  function vueClassement(racine) {
    const donnees = etat.donnees;
    const carte = creer("section", "carte");

    const entete = creer("div", "carte-entete");
    const titres = creer("div");
    titres.append(creer("h2", null, "Classement par adéquation aux critères fondamentaux"));
    titres.append(
      creer(
        "p",
        "note",
        `Univers : ${(donnees.universes || []).join(", ")} · ` +
          `${donnees.counts.ranked} titres classés · ${donnees.counts.excluded} écartés pour ` +
          `données incomplètes · ${donnees.counts.failed} non récupérés`
      )
    );
    entete.append(titres);
    carte.append(entete);

    const indicateurs = creer("div", "indicateurs");
    const premier = donnees.ranked[0];
    if (premier) {
      indicateurs.append(
        indicateur(premier.ticker, "Rang n°1 sur ces critères", true),
        indicateur(nombre(premier.composite, 1), "Meilleur score /100")
      );
    }
    indicateurs.append(
      indicateur(String(donnees.counts.ranked), "Titres classés"),
      indicateur(String(donnees.counts.excluded), "Données incomplètes"),
      indicateur(`${donnees.methodology.target_years} ans`, "Fenêtre visée")
    );
    carte.append(indicateurs);

    if (premier) {
      carte.append(
        creer(
          "p",
          "note-discrete",
          `${premier.ticker} est classé n°1 sur les critères fondamentaux retenus — ` +
            `détail en cliquant sur le titre. Ce rang n'indique ni le moment ni la ` +
            `certitude d'une évolution de cours.`
        )
      );
    }
    racine.append(carte);

    if (etat.appli) {
      const commande = creer("section", "carte");
      commande.append(barreAnalyse());
      const resume = (etat.analyse || {}).resume;
      if (resume) {
        commande.append(
          creer(
            "p",
            "note",
            `Dernière analyse : ${resume.classes} titres classés, ${resume.exclus} écartés, ` +
              `${resume.echecs} non récupérés, en ${Math.round(resume.duree_secondes)} s` +
              (resume.alertes ? ` — ${resume.alertes} alerte(s) déclenchée(s).` : ".")
          )
        );
      }
      racine.append(commande);
    }

    const carteTable = creer("section", "carte");
    carteTable.append(filtres());
    const zone = creer("div", "zone-tableau");
    zone.append(tableauClassement());
    carteTable.append(zone);
    racine.append(carteTable);

    if (etat.selection) {
      const titre = trouverTitre(etat.selection);
      if (titre) racine.append(carteDetail(titre));
    }
  }

  /** Tuile de synthese : le libelle precede la valeur, comme dans un
      tableau de bord — l'oeil lit d'abord de quoi il s'agit. */
  function indicateur(valeur, libelle, accent = false) {
    const noeud = creer("div", "indicateur" + (accent ? " accent" : ""));
    noeud.append(creer("div", "libelle", libelle), creer("div", "valeur", valeur));
    return noeud;
  }

  function filtres() {
    const barre = creer("div", "filtres");

    const boite = creer("div", "champ-recherche");
    const loupe = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    loupe.setAttribute("viewBox", "0 0 16 16");
    loupe.setAttribute("width", "15");
    loupe.setAttribute("height", "15");
    loupe.setAttribute("aria-hidden", "true");
    const trace = document.createElementNS("http://www.w3.org/2000/svg", "path");
    trace.setAttribute("d", "M7 1.8a5.2 5.2 0 1 1 0 10.4A5.2 5.2 0 0 1 7 1.8Zm3.9 9.1 3.3 3.3");
    trace.setAttribute("fill", "none");
    trace.setAttribute("stroke", "currentColor");
    trace.setAttribute("stroke-width", "1.5");
    trace.setAttribute("stroke-linecap", "round");
    loupe.append(trace);
    boite.append(loupe);

    const recherche = creer("input");
    recherche.type = "search";
    recherche.placeholder = "Rechercher un ticker ou un nom…";
    recherche.setAttribute("aria-label", "Rechercher un titre");
    recherche.value = etat.filtres.recherche;
    recherche.addEventListener("input", (evenement) => {
      etat.filtres.recherche = evenement.target.value;
      rendre();
      const champ = document.querySelector(".champ-recherche input");
      if (champ) {
        champ.focus();
        champ.setSelectionRange(champ.value.length, champ.value.length);
      }
    });
    boite.append(recherche);
    barre.append(boite);

    barre.append(
      selecteur(
        "zone",
        "Toutes zones",
        [...new Set(etat.donnees.ranked.map((t) => t.region).filter(Boolean))].sort()
      )
    );
    barre.append(
      selecteur(
        "secteur",
        "Tous secteurs",
        [...new Set(etat.donnees.ranked.map((t) => t.sector).filter(Boolean))].sort()
      )
    );

    const total = etat.donnees.ranked.length;
    const affiches = lignesFiltrees().length;
    barre.append(
      creer(
        "span",
        "compteur",
        affiches === total ? `${total} titres` : `${affiches} sur ${total} titres`
      )
    );
    return barre;
  }

  function selecteur(cle, libelleVide, valeurs) {
    const champ = creer("select");
    const vide = creer("option", null, libelleVide);
    vide.value = "";
    champ.append(vide);
    valeurs.forEach((valeur) => {
      const option = creer("option", null, valeur);
      option.value = valeur;
      if (etat.filtres[cle] === valeur) option.selected = true;
      champ.append(option);
    });
    champ.addEventListener("change", (evenement) => {
      etat.filtres[cle] = evenement.target.value;
      rendre();
    });
    return champ;
  }

  function lignesFiltrees() {
    const { recherche, zone, secteur } = etat.filtres;
    const terme = recherche.trim().toLowerCase();
    let lignes = etat.donnees.ranked.filter((titre) => {
      if (zone && titre.region !== zone) return false;
      if (secteur && titre.sector !== secteur) return false;
      if (!terme) return true;
      return (
        titre.ticker.toLowerCase().includes(terme) ||
        (titre.name || "").toLowerCase().includes(terme)
      );
    });

    const { colonne, ordre } = etat.tri;
    lignes = lignes.slice().sort((a, b) => {
      const va = valeurTri(a, colonne);
      const vb = valeurTri(b, colonne);
      if (va === vb) return a.ticker.localeCompare(b.ticker);
      if (va === null) return 1;
      if (vb === null) return -1;
      return va > vb ? ordre : -ordre;
    });
    return lignes;
  }

  function valeurTri(titre, colonne) {
    if (colonne === "rank") return titre.rank;
    if (colonne === "ticker") return titre.ticker;
    if (colonne === "name") return titre.name || "";
    if (colonne === "sector") return titre.sector || "";
    if (colonne === "region") return titre.region || "";
    if (colonne === "composite") return titre.composite;
    if (colonne === "window") return titre.window_years;
    if (colonne === "coverage") return titre.coverage;
    const pilier = titre.pillars[colonne];
    return pilier ? pilier.score : null;
  }

  function tableauClassement() {
    const colonnes = [
      { cle: "rank", libelle: "Rang", num: true },
      { cle: "ticker", libelle: "Ticker" },
      { cle: "name", libelle: "Nom" },
      { cle: "sector", libelle: "Secteur" },
      { cle: "region", libelle: "Zone" },
      { cle: "composite", libelle: "Score", num: true },
      { cle: "tendance", libelle: "Tendance", num: false, triable: false },
      { cle: "growth", libelle: "Croissance", num: true },
      { cle: "valuation", libelle: "Valorisation", num: true },
      { cle: "profitability", libelle: "Rentabilité", num: true },
      { cle: "balance_sheet", libelle: "Bilan", num: true },
      { cle: "dividend", libelle: "Dividende", num: true },
      { cle: "window", libelle: "Fenêtre", num: true },
      { cle: "coverage", libelle: "Couverture", num: true },
    ];

    const table = creer("table");
    const entete = creer("tr");
    colonnes.forEach((colonne) => {
      const cellule = creer("th", colonne.num ? "num" : null, colonne.libelle);
      if (etat.tri.colonne === colonne.cle) {
        cellule.append(creer("span", "fleche", etat.tri.ordre === 1 ? "▲" : "▼"));
      }
      if (colonne.triable === false) {
        cellule.classList.add("non-triable");
      } else {
        cellule.addEventListener("click", () => {
          if (etat.tri.colonne === colonne.cle) etat.tri.ordre *= -1;
          else etat.tri = { colonne: colonne.cle, ordre: colonne.cle === "rank" ? 1 : -1 };
          rendre();
        });
      }
      entete.append(cellule);
    });
    const thead = creer("thead");
    thead.append(entete);
    table.append(thead);

    const corps = creer("tbody");
    lignesFiltrees().forEach((titre) => {
      const ligne = creer("tr");
      if (etat.selection === titre.ticker) ligne.classList.add("selectionnee");

      // Le rang est toujours ecrit en clair ; la pastille des trois premiers
      // n'ajoute aucune information, elle ne fait que la mettre en avant.
      const celluleRang = creer("td", "num");
      celluleRang.append(
        creer("span", "rang" + (titre.rank <= 3 ? " podium" : ""), String(titre.rank))
      );
      ligne.append(celluleRang);

      const celluleTicker = creer("td", "ticker");
      const lien = creer("a", "lien-titre", titre.ticker);
      lien.href = "#";
      lien.addEventListener("click", (evenement) => {
        evenement.preventDefault();
        etat.selection = titre.ticker;
        rendre();
        const detail = document.getElementById("detail-titre");
        if (detail) detail.scrollIntoView({ behavior: "smooth", block: "start" });
      });
      celluleTicker.append(lien);
      ligne.append(celluleTicker);

      const celluleNom = cellule(titre.name || "—", "nom");
      celluleNom.title = titre.name || "";
      ligne.append(celluleNom);
      ligne.append(cellule(titre.sector || "—", "secondaire"));
      ligne.append(cellule(titre.region || "—", "secondaire"));
      ligne.append(celluleScore(titre.composite));
      ligne.append(celluleTendance(titre.ticker));

      ["growth", "valuation", "profitability", "balance_sheet", "dividend"].forEach((cle) => {
        const pilier = titre.pillars[cle];
        ligne.append(cellule(pilier && pilier.score !== null ? nombre(pilier.score, 1) : "n/d", "num"));
      });

      ligne.append(cellule(`${titre.window_years} ans`, "num secondaire"));
      ligne.append(cellule(`${Math.round(titre.coverage * 100)} %`, "num secondaire"));
      corps.append(ligne);
    });
    table.append(corps);
    return table;
  }

  const cellule = (texte, classe) => creer("td", classe, texte);

  /** Score : barre de grandeur + valeur chiffree (la couleur ne porte
      jamais l'information seule). */
  function celluleScore(valeur) {
    const td = creer("td", "num");
    const boite = creer("div", "cellule-score");
    const jauge = creer("div", "jauge");
    const remplissage = creer("span");
    remplissage.style.width = `${Math.max(0, Math.min(100, valeur || 0))}%`;
    jauge.append(remplissage);
    boite.append(jauge, creer("strong", null, nombre(valeur, 1)));
    td.append(boite);
    return td;
  }

  /**
   * Micro-courbe d'evolution du score, sans axe ni etiquette : elle sert a
   * reperer une tendance d'un coup d'oeil, la valeur exacte restant lisible
   * dans la colonne Score et dans le graphique detaille.
   */
  function celluleTendance(ticker) {
    const td = creer("td");
    const serie = serieHistorique(ticker).slice(-14);
    if (serie.length < 3) {
      td.append(creer("span", "note-discrete", "—"));
      return td;
    }
    const scores = serie.map((point) => point.score);
    const min = Math.min(...scores);
    const max = Math.max(...scores);
    const etendue = max - min || 1;
    const largeur = 62;
    const hauteur = 20;
    const chemin = scores
      .map((score, index) => {
        const x = (index / (scores.length - 1)) * (largeur - 3) + 1.5;
        const y = hauteur - 3 - ((score - min) / etendue) * (hauteur - 6);
        return `${index ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(" ");

    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "tendance");
    svg.setAttribute("viewBox", `0 0 ${largeur} ${hauteur}`);
    svg.setAttribute("aria-label",
      `Tendance du score sur ${serie.length} analyses : de ${nombre(scores[0], 1)} à ` +
      `${nombre(scores[scores.length - 1], 1)}.`);
    const trace = document.createElementNS("http://www.w3.org/2000/svg", "path");
    trace.setAttribute("d", chemin);
    svg.append(trace);
    td.append(svg);
    return td;
  }

  /* ----------------------------------------------------------- detail */
  function trouverTitre(ticker) {
    const donnees = etat.donnees;
    return (
      donnees.ranked.find((t) => t.ticker === ticker) ||
      donnees.excluded.find((t) => t.ticker === ticker) ||
      null
    );
  }

  function carteDetail(titre) {
    const carte = creer("section", "carte");
    carte.id = "detail-titre";

    // ---- en-tete : identite du titre et action watchlist ----------------
    const entete = creer("div", "carte-entete");
    const bloc = creer("div");
    bloc.append(creer("h2", null, `${titre.name || titre.ticker} (${titre.ticker})`));
    const meta = [titre.sector, titre.region].filter(Boolean).join(" · ");
    bloc.append(
      creer(
        "p",
        "note",
        (titre.rank
          ? `Classé n°${titre.rank} sur les critères fondamentaux retenus — voici pourquoi.`
          : "Titre non classé : voir le motif ci-dessous.") + (meta ? `  ${meta}` : "")
      )
    );
    entete.append(bloc);

    const bouton = creer(
      "button",
      "bouton" + (estSuivi(titre.ticker) ? "" : " principal"),
      estSuivi(titre.ticker) ? "★ Retirer de la watchlist" : "☆ Suivre ce titre"
    );
    bouton.style.marginLeft = "auto";
    bouton.addEventListener("click", async () => {
      bouton.disabled = true;
      try {
        await basculerWatchlist(titre.ticker);
      } finally {
        bouton.disabled = false;
      }
      rendre();
    });
    entete.append(bouton);
    carte.append(entete);

    // ---- chiffres cles --------------------------------------------------
    const indicateurs = creer("div", "indicateurs");
    indicateurs.append(
      indicateur(
        titre.composite === null ? "n/d" : nombre(titre.composite, 1),
        "Score composite /100",
        true
      ),
      indicateur(
        titre.price === null || titre.price === undefined
          ? "n/d"
          : `${nombre(titre.price, 2)} ${titre.currency || ""}`,
        "Cours à l'analyse"
      ),
      indicateur(`${titre.window_years} ans`, "Fenêtre d'analyse"),
      indicateur(`${Math.round(titre.coverage * 100)} %`, "Couverture des critères")
    );
    carte.append(indicateurs);

    if (titre.window_years < 5) {
      carte.append(
        encart(
          "attention",
          "⚠",
          `Fenêtre de ${titre.window_years} ans : l'historique fondamental gratuit est plus ` +
            `court pour ce titre (fréquent hors États-Unis). Un TCAM sur ` +
            `${titre.window_years} ans n'est pas strictement comparable à un TCAM sur 5 ans.`
        )
      );
    }
    if (!titre.ranked && titre.exclusion_reason) {
      carte.append(encart("probleme", "✕", titre.exclusion_reason));
    }
    (titre.warnings || []).forEach((message) => {
      carte.append(creer("p", "note-discrete", `ℹ️ ${message}`));
    });

    // ---- deux colonnes : le detail a gauche, la synthese a droite -------
    const grille = creer("div", "detail-grille");

    const colonneCriteres = creer("div");
    colonneCriteres.append(creer("h3", null, "Détail critère par critère"));
    Object.entries(titre.pillars).forEach(([, pilier]) => {
      pilier.criteria.forEach((critere) => colonneCriteres.append(blocCritere(critere, pilier.label)));
    });
    grille.append(colonneCriteres);

    const panneau = creer("aside", "panneau");
    panneau.append(creer("h3", null, "Sous-scores par pilier"));
    Object.entries(titre.pillars).forEach(([, pilier]) => panneau.append(barrePilier(pilier)));
    panneau.append(
      creer(
        "p",
        "note-discrete",
        "Chaque pilier est une moyenne pondérée de ses critères ; le poids indiqué est " +
          "celui qu'il occupe dans le score composite."
      )
    );
    grille.append(panneau);
    carte.append(grille);

    // ---- evolution du score ---------------------------------------------
    const serie = serieHistorique(titre.ticker);
    if (serie.length >= 2) {
      carte.append(creer("h3", null, "Évolution du score composite"));
      carte.append(grapheHistorique(serie));
      carte.append(
        creer(
          "p",
          "note-discrete",
          "Évolution de l'adéquation aux critères d'une analyse à l'autre. " +
            "Ne reflète pas l'évolution du cours."
        )
      );
    }
    return carte;
  }

  function encart(classe, icone, texte) {
    const noeud = creer("div", `encart ${classe}`);
    noeud.append(creer("span", "icone", icone), creer("span", null, texte));
    return noeud;
  }

  function barrePilier(pilier) {
    const ligne = creer("div", "barre-pilier" + (pilier.score === null ? " neutre" : ""));
    ligne.append(creer("div", "nom", `${pilier.label} · ${Math.round(pilier.weight * 100)} %`));
    ligne.append(
      creer("div", "valeur", pilier.score === null ? "n/d" : nombre(pilier.score, 0))
    );
    const piste = creer("div", "barre-piste");
    const remplissage = creer("span");
    remplissage.style.width = `${Math.max(0, Math.min(100, pilier.score || 0))}%`;
    piste.append(remplissage);
    ligne.append(piste);
    return ligne;
  }

  function blocCritere(critere, libellePilier) {
    const bloc = creer("div", "critere");
    const entete = creer("div", "entete");
    entete.append(creer("span", "titre", critere.label));
    entete.append(creer("span", "etiquette", libellePilier));
    entete.append(
      creer("span", "chiffre principal", `${valeurCritere(critere.value, critere.unit)}`)
    );
    entete.append(
      creer(
        "span",
        "chiffre",
        critere.score === null ? "sous-score n/d" : `sous-score ${nombre(critere.score, 1)}/100`
      )
    );
    bloc.append(entete);
    bloc.append(
      creer(
        "div",
        critere.detail ? "explication" : "explication indisponible",
        critere.detail || critere.reason_missing || "—"
      )
    );
    return bloc;
  }

  /* -------------------------------------------------------- graphique */
  function serieHistorique(ticker) {
    const runs = (etat.historique && etat.historique.runs) || [];
    return runs
      .map((run) => ({
        date: run.generated_at,
        score: run.scores && run.scores[ticker] ? run.scores[ticker].score : null,
      }))
      .filter((point) => point.score !== null && point.score !== undefined);
  }

  /**
   * Courbe d'evolution : une seule serie, donc pas de legende (le titre la
   * nomme). Grille discrete, trait de 2 px, marqueur au survol, infobulle.
   */
  function grapheHistorique(serie) {
    const largeur = 720;
    const hauteur = 220;
    const marge = { haut: 12, droite: 14, bas: 26, gauche: 34 };
    const aireL = largeur - marge.gauche - marge.droite;
    const aireH = hauteur - marge.haut - marge.bas;

    const scores = serie.map((p) => p.score);
    const min = Math.max(0, Math.floor((Math.min(...scores) - 5) / 10) * 10);
    const max = Math.min(100, Math.ceil((Math.max(...scores) + 5) / 10) * 10);
    const etendue = max - min || 1;

    const x = (index) =>
      marge.gauche + (serie.length === 1 ? aireL / 2 : (index / (serie.length - 1)) * aireL);
    const y = (score) => marge.haut + aireH - ((score - min) / etendue) * aireH;

    const conteneur = creer("div", "graphe");
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", `0 0 ${largeur} ${hauteur}`);
    svg.setAttribute("role", "img");
    svg.setAttribute(
      "aria-label",
      `Évolution du score composite sur ${serie.length} analyses, de ${nombre(
        scores[0],
        1
      )} à ${nombre(scores[scores.length - 1], 1)} sur 100.`
    );

    const element = (nom, attributs) => {
      const noeud = document.createElementNS("http://www.w3.org/2000/svg", nom);
      Object.entries(attributs).forEach(([cle, valeur]) => noeud.setAttribute(cle, valeur));
      return noeud;
    };

    // Grille horizontale discrete + graduations (chiffres tabulaires).
    const pas = etendue <= 20 ? 5 : 10;
    for (let valeur = min; valeur <= max; valeur += pas) {
      svg.append(
        element("line", {
          x1: marge.gauche, x2: largeur - marge.droite,
          y1: y(valeur), y2: y(valeur),
          stroke: "var(--grid)", "stroke-width": 1,
        })
      );
      const etiquette = element("text", {
        x: marge.gauche - 7, y: y(valeur) + 3.5,
        "text-anchor": "end", fill: "var(--ink-muted)", "font-size": 10,
        "font-variant-numeric": "tabular-nums",
      });
      etiquette.textContent = valeur;
      svg.append(etiquette);
    }

    // Ligne de base
    svg.append(
      element("line", {
        x1: marge.gauche, x2: largeur - marge.droite,
        y1: marge.haut + aireH, y2: marge.haut + aireH,
        stroke: "var(--baseline)", "stroke-width": 1,
      })
    );

    const chemin = serie.map((point, index) => `${index ? "L" : "M"}${x(index)},${y(point.score)}`).join(" ");
    svg.append(
      element("path", {
        d: chemin, fill: "none", stroke: "var(--series)",
        "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round",
      })
    );

    // Dates aux extremites uniquement : etiquetage selectif.
    const dateCourte = (iso) =>
      new Date(iso).toLocaleDateString("fr-FR", { day: "2-digit", month: "short" });
    [0, serie.length - 1].forEach((index, position) => {
      const texte = element("text", {
        x: x(index), y: hauteur - 8,
        "text-anchor": position === 0 ? "start" : "end",
        fill: "var(--ink-muted)", "font-size": 10,
      });
      texte.textContent = dateCourte(serie[index].date);
      svg.append(texte);
    });

    // Couche de survol : reticule + marqueur + infobulle.
    const reticule = element("line", {
      y1: marge.haut, y2: marge.haut + aireH,
      stroke: "var(--baseline)", "stroke-width": 1, opacity: 0,
    });
    const marqueur = element("circle", {
      r: 4.5, fill: "var(--series)", stroke: "var(--surface)", "stroke-width": 2, opacity: 0,
    });
    svg.append(reticule, marqueur);

    const infobulle = creer("div", "infobulle");
    conteneur.append(svg, infobulle);

    const surface = element("rect", {
      x: marge.gauche, y: marge.haut, width: aireL, height: aireH, fill: "transparent",
    });
    svg.append(surface);

    svg.addEventListener("mousemove", (evenement) => {
      const boite = svg.getBoundingClientRect();
      const proportion = (evenement.clientX - boite.left) / boite.width;
      const positionX = proportion * largeur;
      let index = Math.round(((positionX - marge.gauche) / aireL) * (serie.length - 1));
      index = Math.max(0, Math.min(serie.length - 1, index));
      const point = serie[index];
      reticule.setAttribute("x1", x(index));
      reticule.setAttribute("x2", x(index));
      reticule.setAttribute("opacity", 1);
      marqueur.setAttribute("cx", x(index));
      marqueur.setAttribute("cy", y(point.score));
      marqueur.setAttribute("opacity", 1);
      infobulle.innerHTML = `${dateLisible(point.date)}<br><span class="val">${nombre(
        point.score,
        1
      )}</span> / 100`;
      infobulle.style.opacity = 1;
      infobulle.style.left = `${Math.min(
        boite.width - 150,
        Math.max(0, (x(index) / largeur) * boite.width - 60)
      )}px`;
      infobulle.style.top = `${(y(point.score) / hauteur) * boite.height - 52}px`;
    });
    svg.addEventListener("mouseleave", () => {
      reticule.setAttribute("opacity", 0);
      marqueur.setAttribute("opacity", 0);
      infobulle.style.opacity = 0;
    });

    return conteneur;
  }

  /* -------------------------------------------------------- watchlist */
  function vueWatchlist(racine) {
    const carte = creer("section", "carte");
    carte.append(creer("h2", null, "Ma watchlist"));
    carte.append(
      creer(
        "p",
        "note",
        etat.appli
          ? "Enregistrée dans le dossier de l'application : elle vous suit si vous " +
            "copiez ce dossier sur un autre ordinateur."
          : "Enregistrée dans ce navigateur uniquement (aucune donnée n'est transmise). " +
            "Ajoutez un titre depuis le classement."
      )
    );

    const suivis = listeSuivie();
    if (!suivis.length) {
      carte.append(creer("p", "etat-vide", "Aucun titre suivi pour le moment."));
      racine.append(carte);
      return;
    }

    const zone = creer("div", "zone-tableau");
    const table = creer("table");
    const thead = creer("thead");
    const entete = creer("tr");
    ["Ticker", "Nom", "Rang", "Score", "Fenêtre", ""].forEach((libelle, index) => {
      entete.append(creer("th", index >= 2 && index <= 4 ? "num non-triable" : "non-triable", libelle));
    });
    thead.append(entete);
    table.append(thead);

    const corps = creer("tbody");
    suivis.forEach((ticker) => {
      const titre = trouverTitre(ticker);
      const ligne = creer("tr");
      if (!titre) {
        ligne.append(cellule(ticker, "ticker"));
        ligne.append(cellule("absent de la dernière analyse"));
        ligne.append(cellule("—", "num"), cellule("—", "num"), cellule("—", "num"));
      } else {
        const celluleTicker = creer("td", "ticker");
        const lien = creer("a", "lien-titre", titre.ticker);
        lien.href = "#";
        lien.addEventListener("click", (evenement) => {
          evenement.preventDefault();
          etat.selection = titre.ticker;
          etat.vue = "classement";
          rendre();
        });
        celluleTicker.append(lien);
        ligne.append(celluleTicker);
        ligne.append(cellule(titre.name || "—"));
        ligne.append(cellule(titre.rank ? `n°${titre.rank}` : "non classé", "num"));
        ligne.append(cellule(titre.composite === null ? "n/d" : nombre(titre.composite, 1), "num"));
        ligne.append(cellule(`${titre.window_years} ans`, "num"));
      }
      const actions = creer("td");
      const retirer = creer("button", "bouton discret", "Retirer");
      retirer.addEventListener("click", async () => {
        await basculerWatchlist(ticker);
        rendre();
      });
      actions.append(retirer);
      ligne.append(actions);
      corps.append(ligne);
    });
    table.append(corps);
    zone.append(table);
    carte.append(zone);
    racine.append(carte);

    suivis.forEach((ticker) => {
      const titre = trouverTitre(ticker);
      if (titre) racine.append(carteDetail(titre));
    });
  }

  /* ------------------------------------------------------------ exclus */
  function vueExclus(racine) {
    const donnees = etat.donnees;

    const carte = creer("section", "carte");
    carte.append(creer("h2", null, "Titres non classés"));
    carte.append(
      creer(
        "p",
        "note",
        "Ces titres sont écartés du classement plutôt que notés sur des critères manquants : " +
          "un score partiel produirait un rang trompeur."
      )
    );

    if (donnees.excluded.length) {
      const zone = creer("div", "zone-tableau");
      const table = creer("table");
      const thead = creer("thead");
      const entete = creer("tr");
      ["Ticker", "Nom", "Zone", "Fenêtre", "Couverture", "Motif"].forEach((libelle) =>
        entete.append(creer("th", "non-triable", libelle))
      );
      thead.append(entete);
      table.append(thead);
      const corps = creer("tbody");
      donnees.excluded.forEach((titre) => {
        const ligne = creer("tr");
        const celluleTicker = creer("td", "ticker");
        const lien = creer("a", "lien-titre", titre.ticker);
        lien.href = "#";
        lien.addEventListener("click", (evenement) => {
          evenement.preventDefault();
          etat.selection = titre.ticker;
          etat.vue = "classement";
          rendre();
        });
        celluleTicker.append(lien);
        ligne.append(celluleTicker);
        ligne.append(cellule(titre.name || "—"));
        ligne.append(cellule(titre.region || "—"));
        ligne.append(cellule(`${titre.window_years} ans`, "num"));
        ligne.append(cellule(`${Math.round(titre.coverage * 100)} %`, "num"));
        ligne.append(cellule(titre.exclusion_reason || "—"));
        corps.append(ligne);
      });
      table.append(corps);
      zone.append(table);
      carte.append(zone);
    } else {
      carte.append(creer("p", "etat-vide", "Aucun titre exclu lors de cette analyse."));
    }
    racine.append(carte);

    const carteEchecs = creer("section", "carte");
    carteEchecs.append(creer("h2", null, "Échecs de récupération"));
    carteEchecs.append(
      creer(
        "p",
        "note",
        "Données non obtenues au moment de l'analyse (source momentanément indisponible, " +
          "ticker retiré de la cote). Ce n'est pas un jugement sur les fondamentaux du titre."
      )
    );
    if (donnees.failures.length) {
      const liste = creer("ul");
      donnees.failures.forEach((echec) => {
        liste.append(creer("li", "note", `${echec.ticker} — ${echec.reason}`));
      });
      carteEchecs.append(liste);
    } else {
      carteEchecs.append(creer("p", "etat-vide", "Aucun échec lors de cette analyse."));
    }
    racine.append(carteEchecs);
  }

  /* ----------------------------------------------------------- alertes */
  async function vueAlertes(racine) {
    const carte = creer("section", "carte");
    carte.append(creer("h2", null, "Alertes sur seuils personnels"));
    carte.append(
      creer(
        "p",
        "note",
        "Une alerte signale le franchissement d'un seuil que vous définissez, à la fin " +
          "de chaque analyse. Elle ne constitue ni une recommandation ni une incitation à agir."
      )
    );
    racine.append(carte);

    let donnees;
    try {
      donnees = await api("/api/alertes");
    } catch (erreur) {
      carte.append(creer("p", "etat-vide", `Alertes indisponibles : ${erreur.message}`));
      return;
    }
    etat.alertes = donnees;

    // ---- formulaire de creation ----------------------------------------
    const formulaire = creer("div", "filtres");
    const ticker = creer("input");
    ticker.placeholder = "Ticker (ex. AIR.PA)";
    ticker.setAttribute("aria-label", "Ticker");
    ticker.style.paddingLeft = "12px";

    const type = creer("select");
    (donnees.types || []).forEach((cle) => {
      const option = creer("option", null, LIBELLES_ALERTES[cle] || cle);
      option.value = cle;
      type.append(option);
    });

    const seuil = creer("input");
    seuil.type = "number";
    seuil.step = "0.01";
    seuil.placeholder = "Seuil";
    seuil.setAttribute("aria-label", "Seuil");
    seuil.style.paddingLeft = "12px";
    seuil.style.maxWidth = "140px";

    const majParametre = () => {
      const genre = type.value;
      seuil.style.display = genre === "earnings_published" ? "none" : "";
      seuil.placeholder =
        genre === "score_change" ? "Écart en points" :
        genre.startsWith("top_n") ? "N du top" : "Seuil de cours";
    };
    type.addEventListener("change", majParametre);
    majParametre();

    const creerRegle = creer("button", "bouton principal", "Créer la règle");
    creerRegle.addEventListener("click", async () => {
      const genre = type.value;
      const valeur = parseFloat(seuil.value);
      const parametres = {};
      if (genre === "price_above" || genre === "price_below") parametres.threshold = valeur;
      else if (genre === "score_change") parametres.threshold = valeur || 5;
      else if (genre.startsWith("top_n")) parametres.n = Math.round(valeur || 20);
      try {
        await api("/api/alertes", {
          methode: "POST",
          corps: { ticker: ticker.value.trim().toUpperCase(), type: genre, parametres },
        });
        rendre();
      } catch (erreur) {
        alert(`Création impossible : ${erreur.message}`);
      }
    });

    formulaire.append(ticker, type, seuil, creerRegle);
    carte.append(formulaire);

    // ---- regles existantes ---------------------------------------------
    if (donnees.regles.length) {
      const zone = creer("div", "zone-tableau");
      const table = creer("table");
      const thead = creer("thead");
      const entete = creer("tr");
      ["Ticker", "Type", "Paramètre", "État", ""].forEach((libelle) =>
        entete.append(creer("th", "non-triable", libelle))
      );
      thead.append(entete);
      table.append(thead);
      const corps = creer("tbody");
      donnees.regles.forEach((regle) => {
        const ligne = creer("tr");
        ligne.append(cellule(regle.ticker, "ticker"));
        ligne.append(cellule(LIBELLES_ALERTES[regle.kind] || regle.kind));
        ligne.append(
          cellule(
            Object.entries(regle.params || {})
              .map(([cle, valeur]) => `${cle} : ${valeur}`)
              .join(", ") || "—",
            "secondaire"
          )
        );
        ligne.append(
          cellule(regle.last_state === "crossed" ? "seuil franchi" : "en veille", "secondaire")
        );
        const actions = creer("td");
        const supprimer = creer("button", "bouton discret", "Supprimer");
        supprimer.addEventListener("click", async () => {
          await api(`/api/alertes/${regle.id}`, { methode: "DELETE" });
          rendre();
        });
        actions.append(supprimer);
        ligne.append(actions);
        corps.append(ligne);
      });
      table.append(corps);
      zone.append(table);
      carte.append(creer("h3", null, "Règles configurées"));
      carte.append(zone);
    } else {
      carte.append(creer("p", "etat-vide", "Aucune règle configurée."));
    }

    // ---- journal ---------------------------------------------------------
    const journal = creer("section", "carte");
    journal.append(creer("h2", null, "Journal des alertes"));
    if (donnees.journal.length) {
      const zone = creer("div", "zone-tableau");
      const table = creer("table");
      const thead = creer("thead");
      const entete = creer("tr");
      ["Date", "Ticker", "Type", "Message"].forEach((libelle) =>
        entete.append(creer("th", "non-triable", libelle))
      );
      thead.append(entete);
      table.append(thead);
      const corps = creer("tbody");
      donnees.journal.forEach((evenement) => {
        const ligne = creer("tr");
        ligne.append(cellule(dateLisible(evenement.triggered_at), "secondaire"));
        ligne.append(cellule(evenement.ticker, "ticker"));
        ligne.append(cellule(LIBELLES_ALERTES[evenement.kind] || evenement.kind));
        const message = cellule(evenement.message);
        message.style.whiteSpace = "normal";
        ligne.append(message);
        corps.append(ligne);
      });
      table.append(corps);
      zone.append(table);
      journal.append(zone);
    } else {
      journal.append(creer("p", "etat-vide", "Aucune alerte déclenchée à ce jour."));
    }
    racine.append(journal);
  }

  /* ------------------------------------------------------ methodologie */
  function vueMethodologie(racine) {
    const methode = etat.donnees.methodology || {};
    const avertissement = etat.donnees.disclaimer || {};

    const carte = creer("section", "carte");
    carte.append(creer("h2", null, "Méthodologie"));
    carte.append(creer("p", "note", avertissement.what_this_is || ""));

    carte.append(creer("h3", null, "Pondération des piliers"));
    const zonePiliers = creer("div", "zone-tableau");
    const tablePiliers = creer("table");
    const enteteP = creer("tr");
    ["Pilier", "Poids"].forEach((l) => enteteP.append(creer("th", "non-triable", l)));
    const theadP = creer("thead");
    theadP.append(enteteP);
    tablePiliers.append(theadP);
    const corpsP = creer("tbody");
    (methode.pillars || []).forEach((pilier) => {
      const ligne = creer("tr");
      ligne.append(cellule(pilier.label), cellule(`${Math.round(pilier.weight * 100)} %`, "num"));
      corpsP.append(ligne);
    });
    tablePiliers.append(corpsP);
    zonePiliers.append(tablePiliers);
    carte.append(zonePiliers);

    carte.append(creer("h3", null, "Critères et barèmes"));
    const zoneCriteres = creer("div", "zone-tableau");
    const tableCriteres = creer("table");
    const enteteC = creer("tr");
    ["Critère", "Pilier", "Poids dans le pilier", "Sens", "Barème (valeur → score)"].forEach((l) =>
      enteteC.append(creer("th", "non-triable", l))
    );
    const theadC = creer("thead");
    theadC.append(enteteC);
    tableCriteres.append(theadC);
    const corpsC = creer("tbody");
    (methode.criteria || []).forEach((critere) => {
      const ligne = creer("tr");
      ligne.append(cellule(critere.label));
      ligne.append(cellule(critere.pillar_label));
      ligne.append(cellule(`${Math.round(critere.weight * 100)} %`, "num"));
      ligne.append(cellule(critere.higher_is_better ? "plus haut = mieux" : "plus bas = mieux"));
      ligne.append(
        cellule((critere.points || []).map(([x, y]) => `${x} → ${y}`).join(" ; "))
      );
      corpsC.append(ligne);
    });
    tableCriteres.append(corpsC);
    zoneCriteres.append(tableCriteres);
    carte.append(zoneCriteres);

    carte.append(creer("h3", null, "Règles d'exclusion"));
    const regles = creer("ul");
    [
      `Fenêtre visée : ${methode.target_years} ans ; minimum requis : ${methode.min_years} ans.`,
      `Couverture minimale des critères pour être classé : ${Math.round(
        (methode.min_weight_coverage || 0) * 100
      )} %.`,
      `Un pilier dont la couverture est inférieure à ${Math.round(
        (methode.min_pillar_coverage || 0) * 100
      )} % est neutralisé et son poids redistribué.`,
      `Un titre sans dividende reçoit un score neutre de ${methode.no_dividend_score}/100 sur ce ` +
        `pilier : les valeurs de croissance ne sont pas pénalisées.`,
      "Un critère non calculable est marqué n/d, jamais remplacé par une valeur favorable.",
    ].forEach((texte) => regles.append(creer("li", "note", texte)));
    carte.append(regles);

    carte.append(creer("h3", null, "Limites"));
    carte.append(creer("p", "note", avertissement.data_limits || ""));
    carte.append(creer("p", "note", avertissement.what_this_is_not || ""));
    racine.append(carte);
  }

  /* ------------------------------------------------------------ demarrage */
  appliquerTheme(themeActuel());
  const boutonTheme = $("#bascule-theme");
  if (boutonTheme) {
    boutonTheme.addEventListener("click", () => {
      const suivant = THEMES[(THEMES.indexOf(themeActuel()) + 1) % THEMES.length];
      appliquerTheme(suivant);
    });
  }

  document.querySelectorAll("nav.onglets button").forEach((bouton) => {
    bouton.addEventListener("click", () => {
      etat.vue = bouton.dataset.vue;
      rendre();
    });
  });

  charger();
})();
