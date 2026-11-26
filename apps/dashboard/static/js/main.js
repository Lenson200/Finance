(() => {
  const OFFLINE_DB = "pocketfinance-offline";
  const OFFLINE_DB_VERSION = 1;
  const openOfflineDatabase = () => new Promise((resolve, reject) => {
    const request = indexedDB.open(OFFLINE_DB, OFFLINE_DB_VERSION);
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains("transactions")) {
        request.result.createObjectStore("transactions", { keyPath: "id" });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  const readOfflineTransactions = async () => {
    const database = await openOfflineDatabase();
    return new Promise((resolve, reject) => {
      const transaction = database.transaction("transactions", "readonly");
      const request = transaction.objectStore("transactions").getAll();
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
      transaction.oncomplete = () => database.close();
    });
  };
  const writeOfflineTransaction = async (item) => {
    const database = await openOfflineDatabase();
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
  const removeOfflineTransaction = async (id) => {
    const database = await openOfflineDatabase();
    return new Promise((resolve, reject) => {
      const transaction = database.transaction("transactions", "readwrite");
      transaction.objectStore("transactions").delete(id);
      transaction.oncomplete = () => {
        database.close();
        resolve();
      };
      transaction.onerror = () => reject(transaction.error);
    });
  };

  const networkStatus = document.getElementById("network-status");
  const offlineStatus = document.getElementById("offline-connection-status");
  const updateNetworkStatus = () => {
    const text = navigator.onLine
      ? "Online · offline queue ready"
      : "Offline · entries are saved on this device";
    if (networkStatus) networkStatus.textContent = text;
    if (offlineStatus) offlineStatus.textContent = text;
  };
  updateNetworkStatus();
  window.addEventListener("online", updateNetworkStatus);
  window.addEventListener("offline", updateNetworkStatus);

  let serviceWorkerRegistration;
  const requestOfflineSync = async () => {
    if (!("serviceWorker" in navigator)) return;
    try {
      const registration = serviceWorkerRegistration
        || await navigator.serviceWorker.ready;
      if (registration.sync) {
        await registration.sync.register("pocketfinance-transactions");
      } else if (navigator.onLine) {
        (registration.active || navigator.serviceWorker.controller)
          ?.postMessage({ type: "SYNC_OFFLINE_TRANSACTIONS" });
      }
    } catch (error) {
      const status = document.getElementById("offline-recording-status");
      if (status) {
        status.textContent = "Background sync could not start. Use Sync now when connected.";
      }
    }
  };
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/service-worker.js")
      .then((registration) => {
        serviceWorkerRegistration = registration;
        if (navigator.onLine) requestOfflineSync();
      })
      .catch(() => {
        if (networkStatus) {
          networkStatus.textContent = "Offline support is unavailable in this browser connection.";
        }
      });
    navigator.serviceWorker.addEventListener("message", (event) => {
      if (event.data?.type === "OFFLINE_QUEUE_UPDATED") renderOfflineQueue();
    });
  } else if (networkStatus) {
    networkStatus.textContent = "Offline storage is not supported in this browser.";
  }
  window.addEventListener("online", requestOfflineSync);

  const offlineConfigNode = document.getElementById("offline-transaction-config");
  const offlineConfig = offlineConfigNode
    ? JSON.parse(offlineConfigNode.textContent)
    : null;
  const queueList = document.getElementById("offline-queue-list");
  const renderOfflineQueue = async () => {
    if (!queueList || !offlineConfig) return;
    try {
      const items = (await readOfflineTransactions())
        .filter((item) => item.userId === offlineConfig.user_id)
        .sort((left, right) => right.createdAt.localeCompare(left.createdAt));
      queueList.replaceChildren();
      if (!items.length) {
        const empty = document.createElement("p");
        empty.className = "muted small";
        empty.textContent = "No offline transactions on this device.";
        queueList.append(empty);
        return;
      }
      items.forEach((item) => {
        const row = document.createElement("div");
        row.className = "recent-row";
        const description = document.createElement("span");
        description.className = "recent-description";
        const title = document.createElement("strong");
        title.textContent = `${item.payload.description || item.payload.kind} · ${item.payload.amount}`;
        const detail = document.createElement("small");
        detail.textContent = `${item.payload.occurred_date} · ${item.state === "queued" ? "Waiting to sync" : item.state === "synced" ? "Synced" : `Needs review: ${item.message}`}`;
        description.append(title, detail);
        row.append(description);

        if (item.state === "conflict") {
          const retry = document.createElement("button");
          retry.className = "button button-secondary";
          retry.type = "button";
          retry.textContent = "Retry";
          retry.addEventListener("click", async () => {
            item.state = "queued";
            item.message = "";
            await writeOfflineTransaction(item);
            renderOfflineQueue();
            requestOfflineSync();
          });
          row.append(retry);
        }
        if (item.state === "conflict" || item.state === "synced") {
          const remove = document.createElement("button");
          remove.className = "button button-secondary";
          remove.type = "button";
          remove.textContent = item.state === "synced" ? "Dismiss" : "Remove local copy";
          remove.addEventListener("click", async () => {
            if (
              item.state === "conflict"
              && !window.confirm("Remove this transaction from this device's offline queue?")
            ) return;
            await removeOfflineTransaction(item.id);
            renderOfflineQueue();
          });
          row.append(remove);
        }
        queueList.append(row);
      });
    } catch (error) {
      queueList.textContent = "Offline storage is unavailable. Your queued transactions could not be loaded.";
    }
  };
  renderOfflineQueue();
  document.getElementById("sync-offline-transactions")
    ?.addEventListener("click", requestOfflineSync);

  const timezoneSyncUrl = document.body.dataset.timezoneSyncUrl;
  if (timezoneSyncUrl) {
    const detectedTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    const csrfToken = document.querySelector(
      "#timezone-sync-token input[name='csrfmiddlewaretoken']",
    )?.value;
    const syncError = document.getElementById("timezone-sync-error");

    if (detectedTimezone && csrfToken) {
      fetch(timezoneSyncUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrfToken,
        },
        body: JSON.stringify({ timezone: detectedTimezone }),
      })
        .then((response) => {
          if (!response.ok) throw new Error("Timezone sync request failed.");
          return response.json();
        })
        .then((result) => {
          if (result.changed) window.location.reload();
        })
        .catch(() => {
          if (syncError) syncError.hidden = false;
        });
    } else if (syncError) {
      syncError.hidden = false;
    }
  }

  const addButton = document.getElementById("floating-add");
  const actionsDialog = document.getElementById("quick-actions-dialog");

  if (addButton && actionsDialog && typeof actionsDialog.showModal === "function") {
    addButton.addEventListener("click", () => actionsDialog.showModal());

    const closeButton = actionsDialog.querySelector("[data-close-dialog]");
    closeButton?.addEventListener("click", () => actionsDialog.close());
    actionsDialog.addEventListener("click", (event) => {
      if (event.target === actionsDialog) actionsDialog.close();
    });
  }

  const accountForm = document.getElementById("account-form");
  if (accountForm) {
    const accountType = accountForm.elements.namedItem("account_type");
    const debtTermField = accountForm.querySelector('[data-field="debt_term"]');
    const updateDebtTerm = () => {
      if (!debtTermField || !accountType) return;
      debtTermField.hidden = !["credit_card", "debt"].includes(accountType.value);
    };
    accountType?.addEventListener("change", updateDebtTerm);
    updateDebtTerm();
  }

  const form = document.getElementById("quick-transaction-form");
  if (!form) return;

  const field = (name) => form.elements.namedItem(name);
  const kind = field("kind");
  const account = field("account");
  const destination = field("destination_account");
  const category = field("category");
  const amount = field("amount");
  const description = field("description");
  const refund = field("refund_of");
  const currenciesNode = document.getElementById("account-currencies");
  const currencies = currenciesNode ? JSON.parse(currenciesNode.textContent) : {};
  const destinationField = form.querySelector('[data-field="destination_account"]');
  const categoryField = form.querySelector('[data-field="category"]');
  const refundField = form.querySelector('[data-field="refund_of"]');
  const kindField = form.querySelector('[data-field="kind"]');
  const accountLabel = form.querySelector('[data-field="account"] label');
  const amountLabel = form.querySelector('[data-field="amount"] label');
  const title = document.getElementById("entry-title");
  const baseCurrency = form.dataset.baseCurrency || "";
  const offlineRecordingStatus = document.getElementById("offline-recording-status");
  const rateData = offlineConfig?.rates || {};
  const idempotencyKey = field("idempotency_key");
  const occurredDate = field("occurred_date");
  const templateSave = field("save_as_template");

  const transactionPayload = () => {
    const selectedAccount = account?.value || "";
    const savedRate = rateData[selectedAccount];
    return {
      account_id: selectedAccount,
      destination_account_id: destination?.value || null,
      category_id: category?.value || null,
      refund_of_id: refund?.value || null,
      kind: kind?.value || "",
      amount: amount?.value || "",
      description: description?.value || "",
      idempotency_key: idempotencyKey?.value || "",
      occurred_date: occurredDate?.value || "",
      exchange_rate: savedRate?.rate || null,
    };
  };
  const queueTransaction = async (payload) => {
    if (!payload.exchange_rate) {
      if (offlineRecordingStatus) {
        offlineRecordingStatus.textContent = "No saved exchange rate is available for this account. Connect while online to record it safely.";
      }
      return false;
    }
    const item = {
      id: payload.idempotency_key,
      userId: offlineConfig.user_id,
      endpoint: offlineConfig.endpoint,
      payload,
      state: "queued",
      message: "",
      createdAt: new Date().toISOString(),
    };
    try {
      await writeOfflineTransaction(item);
      if (offlineRecordingStatus) {
        const snapshot = rateData[account.value]?.fetched_at;
        offlineRecordingStatus.textContent = snapshot
          ? `Saved on this device. It will sync when online using the saved exchange rate from ${new Date(snapshot).toLocaleString()}.`
          : "Saved on this device. It will sync when online.";
      }
      if (amount) amount.value = "";
      if (description) description.value = "";
      if (idempotencyKey) idempotencyKey.value = crypto.randomUUID();
      renderOfflineQueue();
      requestOfflineSync();
      return true;
    } catch (error) {
      if (offlineRecordingStatus) offlineRecordingStatus.textContent = "Could not save this transaction locally. Keep this page open and try again.";
      return false;
    }
  };

  form.addEventListener("submit", async (event) => {
    if (templateSave?.checked) {
      if (!navigator.onLine) {
        event.preventDefault();
        if (offlineRecordingStatus) {
          offlineRecordingStatus.textContent = "Saving quick-entry templates requires an internet connection.";
        }
      }
      return;
    }
    event.preventDefault();
    if (offlineRecordingStatus) offlineRecordingStatus.textContent = "";
    const payload = transactionPayload();
    if (!payload.account_id || !payload.amount || !payload.occurred_date) {
      if (offlineRecordingStatus) offlineRecordingStatus.textContent = "Choose an account, amount, and transaction date.";
      return;
    }
    if (payload.kind === "transfer" && !payload.destination_account_id) {
      if (offlineRecordingStatus) offlineRecordingStatus.textContent = "Choose a destination account for this transfer.";
      return;
    }
    if (payload.kind !== "transfer" && !payload.category_id) {
      if (offlineRecordingStatus) offlineRecordingStatus.textContent = "Choose a category for this transaction.";
      return;
    }

    if (!navigator.onLine) {
      await queueTransaction(payload);
      return;
    }
    if (!payload.exchange_rate) {
      form.submit();
      return;
    }

    try {
      const csrf = form.querySelector("input[name='csrfmiddlewaretoken']")?.value;
      const response = await fetch(form.dataset.offlineEndpoint, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrf,
        },
        body: JSON.stringify(payload),
      });
      const result = await response.json();
      if (!response.ok) {
        if (offlineRecordingStatus) offlineRecordingStatus.textContent = result.detail || "The transaction could not be recorded.";
        return;
      }
      window.location.assign(form.dataset.successUrl);
    } catch (error) {
      await queueTransaction(payload);
    }
  });

  const updateFields = () => {
    if (!kind) return;

    const selectedKind = kind.value;
    if (kindField) kindField.hidden = true;
    if (destinationField) destinationField.hidden = selectedKind !== "transfer";
    if (categoryField) categoryField.hidden = selectedKind === "transfer";
    if (refundField) refundField.hidden = selectedKind !== "income";
    if (accountLabel) {
      accountLabel.textContent = selectedKind === "income"
        ? "Received into"
        : selectedKind === "transfer"
          ? "From account"
          : "Paid from";
    }
    if (amountLabel) {
      amountLabel.textContent = `Amount (${currencies[account?.value] || baseCurrency})`;
    }
    if (title) {
      title.textContent = selectedKind === "income"
        ? "Record income"
        : selectedKind === "transfer"
          ? "Transfer between accounts"
          : "Record expense";
    }

    document.querySelectorAll("[data-kind-button]").forEach((button) => {
      button.classList.toggle("selected", button.dataset.kindButton === selectedKind);
    });
    document.querySelectorAll(".category-group").forEach((group) => {
      group.hidden = group.dataset.kind !== selectedKind;
    });
    category?.querySelectorAll("option[data-kind]").forEach((option) => {
      option.hidden = option.dataset.kind !== selectedKind;
      if (option.hidden && option.selected) category.value = "";
    });
    document.querySelectorAll(".suggestions").forEach((section) => {
      const buttons = [...section.querySelectorAll(
        ".recent-suggestion-chip, .saved-template-chip",
      )];
      let visibleSuggestions = 0;
      buttons.forEach((button) => {
        button.hidden = button.dataset.kind !== selectedKind;
        if (!button.hidden) visibleSuggestions += 1;
      });
      const emptyMessage = section.querySelector(".no-kind-suggestions");
      if (emptyMessage) emptyMessage.hidden = visibleSuggestions > 0;
    });
  };

  kind?.addEventListener("change", () => {
    if (category) category.value = "";
    updateFields();
  });
  account?.addEventListener("change", updateFields);

  document.querySelectorAll("[data-kind-button]").forEach((button) => {
    button.addEventListener("click", () => {
      if (!kind) return;
      kind.value = button.dataset.kindButton;
      if (category) category.value = "";
      updateFields();
    });
  });

  const applySuggestion = (button, { includeDestination = false } = {}) => {
    if (kind) kind.value = button.dataset.kind || kind.value;
    if (account) account.value = button.dataset.account || account.value;
    if (includeDestination && destination) {
      destination.value = button.dataset.destination || "";
    }
    if (category) category.value = button.dataset.category || "";
    if (refund) refund.value = button.dataset.refundOf || "";
    if (amount) amount.value = button.dataset.amount || "";
    if (description) description.value = button.dataset.description || "";
    updateFields();
    amount?.focus();
  };

  document.querySelectorAll(".recent-suggestion-chip").forEach((button) => {
    button.addEventListener("click", () => applySuggestion(button, { includeDestination: true }));
  });
  document.querySelectorAll(".saved-template-chip").forEach((button) => {
    button.addEventListener("click", () => applySuggestion(button, { includeDestination: true }));
  });

  document.querySelectorAll(".category-chip").forEach((button) => {
    button.addEventListener("click", () => {
      if (!category) return;
      category.value = button.dataset.category || "";
      form.querySelectorAll(".category-chip").forEach((chip) => {
        chip.classList.toggle("selected", chip === button);
      });
    });
  });

  updateFields();

  const saveTemplate = field("save_as_template");
  const templateName = form.querySelector('[data-field="template_name"]');
  if (saveTemplate && templateName) {
    const updateTemplateName = () => {
      templateName.hidden = !saveTemplate.checked;
    };
    saveTemplate.addEventListener("change", updateTemplateName);
    updateTemplateName();
  }

  if (new URL(window.location.href).searchParams.get("template") === "1") {
    const templateButton = document.querySelector(".saved-template-chip")
      || document.querySelector(".recent-suggestion-chip");
    templateButton?.click();
  }
})();
