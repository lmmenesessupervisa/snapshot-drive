// Auth helpers — CSRF-aware fetch and logout button.

(function () {
  function csrfToken() {
    const m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.getAttribute("content") : "";
  }

  // Wrapper for state-changing fetches. Caller passes URL + opts (same as fetch).
  window.apiFetch = async function (url, opts) {
    opts = opts || {};
    const headers = Object.assign(
      { "Content-Type": "application/json", "X-CSRF-Token": csrfToken() },
      opts.headers || {}
    );
    const res = await fetch(url, Object.assign({}, opts, { headers }));
    if (res.status === 401) {
      window.location = "/auth/login";
      return Promise.reject(new Error("unauthenticated"));
    }
    return res;
  };

  document.addEventListener("DOMContentLoaded", function () {
    var btn = document.getElementById("logout-btn");
    if (btn) {
      btn.addEventListener("click", async function () {
        try {
          await apiFetch("/auth/logout", { method: "POST" });
        } catch (e) {
          // If unauthenticated redirect already triggered, swallow the error.
          if (e.message === "unauthenticated") return;
        }
        window.location = "/auth/login";
      });
    }
  });
})();
