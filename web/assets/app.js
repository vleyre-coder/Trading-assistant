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

  const estSuivi = (ticker) => lireWatchlist().includes(ticker);

  function basculerWatchlist(ticker) {
    const liste = lireWatchlist();
    const index = liste.indexOf(ticker);
    if (index >= 0) liste.splice(index, 1);
    else liste.push(ticker);
    ecrireWatchlist(liste);
  }

  /* ------------------------------------------------------- chargement */
  async function charger() {
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
      watchlist: vueWatchlist,
      exclus: vueExclus,
      methodologie: vueMethodologie,
    };
    (vues[etat.vue] || vueClassement)(contenu);
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
    bouton.addEventListener("click", () => {
      basculerWatchlist(titre.ticker);
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
        "Enregistrée dans ce navigateur uniquement (aucune donnée n'est transmise). " +
          "Ajoutez un titre depuis le classement."
      )
    );

    const suivis = lireWatchlist();
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
      const retirer = creer("button", "bouton", "Retirer");
      retirer.addEventListener("click", () => {
        basculerWatchlist(ticker);
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
