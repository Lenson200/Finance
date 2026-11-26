const CACHE_NAME = "pocketfinance-shell-v1";
const PAGE_CACHE = "pocketfinance-entry-v1";
const DB_NAME = "pocketfinance-offline";
const DB_VERSION = 1;
const STATIC_ASSETS = [
  "/static/css/app.css",
  "/static/js/main.js",
  "/static/offline.html",
  "/static/manifest.webmanifest",
  "/static/icons/pocketfinance.svg",
];

const openDatabase = () => new Promise((resolve, reject) => {
  const request = indexedDB.open(DB_NAME, DB_VERSION);
  request.onupgradeneeded = () => {
    const database = request.result;
    if (!database.objectStoreNames.contains("transactions")) {
      database.createObjectStore("transactions", { keyPath: "id" });
    }
  };
  request.onsuccess = () => resolve(request.result);
  request.onerror = () => reject(request.error);
});

const readQueued = async () => {
  const database = await openDatabase();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction("transactions", "readonly");
    const request = transaction.objectStore("transactions").getAll();
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
    transaction.oncomplete = () => database.close();
  });
};

const updateQueued = async (item) => {
  const database = await openDatabase();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction("transactions", "readwrite");
    transaction.objectStore("transactions").put(item);
    transaction.oncomplete = () => {
      database.close();
      resolve();
    };
    transaction.onerror = () => reject(transaction.error);
  });
};

const notifyClients = async () => {
  const clients = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
  clients.forEach((client) => client.postMessage({ type: "OFFLINE_QUEUE_UPDATED" }));
};

const syncTransactions = async () => {
  const contextResponse = await fetch("/api/offline/context/", {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!contextResponse.ok) return;
  const context = await contextResponse.json();
  const items = (await readQueued()).filter(
    (item) => item.userId === context.user_id && item.state === "queued",
  );

  for (const item of items) {
    let response;
    try {
      response = await fetch(item.endpoint, {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": context.csrf_token,
        },
        body: JSON.stringify(item.payload),
      });
    } catch (error) {
      throw error;
    }

    if (response.ok) {
      item.state = "synced";
      item.message = "Saved to your account.";
    } else if (response.status >= 500 || response.status === 429) {
      throw new Error(`Temporary sync failure (${response.status}).`);
    } else {
      const result = await response.json().catch(() => ({}));
      item.state = "conflict";
      item.message = result.detail || `The server rejected this transaction (${response.status}).`;
    }
    item.updatedAt = new Date().toISOString();
    await updateQueued(item);
  }
  await notifyClients();
};

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => cache.addAll(STATIC_ASSETS))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys
          .filter((key) => ![CACHE_NAME, PAGE_CACHE].includes(key))
          .map((key) => caches.delete(key)),
      ))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const requestUrl = new URL(event.request.url);
  if (requestUrl.origin !== self.location.origin) return;

  if (event.request.method === "POST" && requestUrl.pathname === "/logout/") {
    event.respondWith(
      caches.delete(PAGE_CACHE).then(() => fetch(event.request)),
    );
    return;
  }

  if (event.request.method !== "GET") return;
  if (requestUrl.pathname.startsWith("/api/")) return;

  if (requestUrl.pathname.startsWith("/static/")) {
    event.respondWith(
      caches.match(event.request).then(
        (cached) => cached || fetch(event.request).then((response) => {
          if (response.ok) {
            const copy = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
          }
          return response;
        }),
      ),
    );
    return;
  }

  if (event.request.mode === "navigate" && requestUrl.pathname === "/activity/new/") {
    const entryPage = new Request(`${self.location.origin}/activity/new/`);
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          if (
            response.ok
            && !response.redirected
            && new URL(response.url).pathname === "/activity/new/"
          ) {
            const copy = response.clone();
            caches.open(PAGE_CACHE).then(
              (cache) => cache.put(entryPage, copy),
            );
          }
          return response;
        })
        .catch(async () => (
          await caches.match(entryPage, { cacheName: PAGE_CACHE })
          || caches.match("/static/offline.html")
        )),
    );
    return;
  }

  if (event.request.mode === "navigate") {
    event.respondWith(
      fetch(event.request).catch(
        () => caches.match("/static/offline.html"),
      ),
    );
  }
});

self.addEventListener("sync", (event) => {
  if (event.tag === "pocketfinance-transactions") {
    event.waitUntil(syncTransactions());
  }
});

self.addEventListener("message", (event) => {
  if (event.data?.type === "SYNC_OFFLINE_TRANSACTIONS") {
    event.waitUntil(syncTransactions());
  }
});
