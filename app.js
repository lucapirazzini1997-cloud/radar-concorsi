import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.2/firebase-app.js";
import { getAuth, GoogleAuthProvider, signInWithPopup, signOut, onAuthStateChanged } from "https://www.gstatic.com/firebasejs/10.12.2/firebase-auth.js";
import { getFirestore, doc, setDoc, getDoc, collection, addDoc, deleteDoc, onSnapshot, query, orderBy, limit, serverTimestamp } from "https://www.gstatic.com/firebasejs/10.12.2/firebase-firestore.js";
import { firebaseConfig } from "./firebase-config.js";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (ts) => { if (!ts) return ""; const d = ts.toDate ? ts.toDate() : new Date(ts); return d.toLocaleDateString("it-IT", { day: "2-digit", month: "short", year: "numeric" }); };

if (firebaseConfig.apiKey === "INSERISCI") {
  document.querySelector("main").innerHTML = '<section class="card"><h2>Configurazione mancante</h2><p>Inserisci i dati del progetto Firebase in <code>firebase-config.js</code>.</p></section>';
  throw new Error("firebase-config.js non configurato");
}

const app = initializeApp(firebaseConfig);
const auth = getAuth(app);
const db = getFirestore(app);
let unsubs = [];

$("loginBtn").onclick = () => signInWithPopup(auth, new GoogleAuthProvider()).catch(e => alert("Accesso non riuscito: " + e.message));

onAuthStateChanged(auth, async (user) => {
  unsubs.forEach(u => u()); unsubs = [];
  if (!user) {
    $("userBox").innerHTML = "";
    $("landing").classList.remove("hidden");
    $("app").classList.add("hidden");
    return;
  }
  $("landing").classList.add("hidden");
  $("app").classList.remove("hidden");
  $("userBox").innerHTML = `<span class="row">${user.photoURL ? `<img class="avatar" src="${esc(user.photoURL)}" alt="">` : ""}<button id="logoutBtn">Esci</button></span>`;
  $("logoutBtn").onclick = () => signOut(auth);

  // profilo utente (email usata per il riepilogo)
  const uref = doc(db, "users", user.uid);
  const snap = await getDoc(uref);
  const prev = snap.exists() ? snap.data() : {};
  await setDoc(uref, {
    email: user.email, name: user.displayName || "",
    emailDaily: prev.emailDaily ?? true,
    ...(snap.exists() ? {} : { createdAt: serverTimestamp() })
  }, { merge: true });
  $("emailToggle").checked = prev.emailDaily ?? true;
  $("emailToggle").onchange = (e) => setDoc(uref, { emailDaily: e.target.checked }, { merge: true });

  const follows = collection(db, "users", user.uid, "follows");
  unsubs.push(onSnapshot(query(follows, orderBy("createdAt", "desc")), (qs) => {
    const items = qs.docs.map(d => ({ id: d.id, ...d.data() }));
    $("followList").innerHTML = items.length ? items.map(f => `
      <li class="${f.lastChange && (Date.now() - f.lastChange.toMillis()) < 3 * 864e5 ? "new" : ""}">
        <div class="title">${esc(f.title)}</div>
        <div class="meta">${esc(f.ente)} · ${esc(f.stato)} · <a href="${esc(f.url)}" target="_blank" rel="noopener">pagina del concorso</a></div>
        <div class="meta">${f.lastCheck ? "Ultimo controllo: " + fmt(f.lastCheck) : "In attesa del primo controllo"}${f.lastChange ? " · Ultima novità: " + fmt(f.lastChange) : ""}${f.lastError ? " · ⚠ " + esc(f.lastError) : ""}</div>
        ${f.lastSummary ? `<div class="summary">${esc(f.lastSummary)}</div>` : ""}
        <div class="actions"><button data-del="${f.id}">Rimuovi</button></div>
      </li>`).join("") : '<li class="muted">Nessun concorso ancora. Aggiungine uno qui sopra.</li>';
    document.querySelectorAll("[data-del]").forEach(b => b.onclick = () => {
      if (confirm("Smettere di seguire questo concorso?")) deleteDoc(doc(follows, b.dataset.del));
    });
  }));

  unsubs.push(onSnapshot(query(collection(db, "feed"), orderBy("dataPubblicazione", "desc"), limit(30)), (qs) => {
    const items = qs.docs.map(d => ({ id: d.id, ...d.data() }));
    $("feedList").innerHTML = items.length ? items.map(b => `
      <li>
        <div class="title">${esc(b.titolo)}</div>
        <div class="meta">${esc(b.ente)} · ${esc((b.sedi || []).join(", "))} · ${b.numPosti ? b.numPosti + " posti · " : ""}scadenza ${esc((b.dataScadenza || "").slice(0, 10))}</div>
        <div class="actions"><a href="${esc(b.url)}" target="_blank" rel="noopener">Apri bando</a>
        <button data-follow="${b.id}">Segui</button></div>
      </li>`).join("") : '<li class="muted">Nessun bando ancora: il primo controllo giornaliero li caricherà.</li>';
    document.querySelectorAll("[data-follow]").forEach(btn => btn.onclick = async () => {
      const b = items.find(x => x.id === btn.dataset.follow);
      await addDoc(follows, { title: b.titolo, ente: b.ente || "", url: b.url, stato: "Da valutare", createdAt: serverTimestamp() });
      btn.textContent = "Seguito ✓"; btn.disabled = true;
    });
  }));

  $("addForm").onsubmit = async (e) => {
    e.preventDefault();
    await addDoc(follows, {
      title: $("fTitle").value.trim(), ente: $("fEnte").value.trim(),
      url: $("fUrl").value.trim(), stato: $("fStato").value, createdAt: serverTimestamp()
    });
    e.target.reset();
  };
});
