/* theme: system by default; the toggle remembers an explicit choice */
(() => {
  const root = document.documentElement;
  const apply = t => { if (t) root.dataset.theme = t; else delete root.dataset.theme; };
  try { apply(localStorage.getItem("theme")); } catch (e) {}
  document.addEventListener("DOMContentLoaded", () => {
    const btn = document.getElementById("theme");
    if (btn) btn.onclick = () => {
      const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
      const next = dark ? "light" : "dark"; apply(next);
      try { localStorage.setItem("theme", next); } catch (e) {}
    };
    document.querySelectorAll(".copy").forEach(b => b.onclick = async () => {
      const code = b.parentElement.querySelector("code").innerText;
      try { await navigator.clipboard.writeText(code); b.textContent = "Copied"; } catch (e) { b.textContent = "Select & copy"; }
      setTimeout(() => (b.textContent = "Copy"), 1500);
    });
  });
})();
