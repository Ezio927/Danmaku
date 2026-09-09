(function () {
  "use strict";

  var MAX_ITEMS = 1000;
  var RECONNECT_DELAYS_MS = [500, 1000, 2000, 4000, 5000];
  var HELLO_FRAME = '{"protocolVersion":1,"type":"hello","payload":{}}';
  var SCROLL_SLACK_PX = 24;
  var PRESENTATION_INTERVAL_MS = 3000;
  var TICK_INTERVAL_MS = 200;
  var HEALTH_PROBE_INTERVAL_MS = 5000;

  // Deterministic Super Chat tier/color buckets, keyed on the canonical
  // ``data.amountMilliCny`` integer. The label is rendered as text and the
  // ``css`` suffix selects the card accent color.
  var SUPER_CHAT_TIERS = [
    { min: 10000000, label: "Gold", css: "gold" },
    { min: 2000000, label: "Red", css: "red" },
    { min: 1000000, label: "Pink", css: "pink" },
    { min: 500000, label: "Purple", css: "purple" },
    { min: 100000, label: "Indigo", css: "indigo" },
    { min: 50000, label: "Cyan", css: "cyan" },
    { min: 0, label: "Blue", css: "blue" }
  ];

  // Fixed, deterministic status labels. They are rendered only as text and
  // never interpolate exception details, secrets, or user content. The local
  // service state comes from the existing loopback /health route; the Host feed
  // state comes from the WebSocket lifecycle and its bounded reconnect schedule.
  var SERVICE_STATUS_TEXT = {
    available: "Local service: available",
    unavailable: "Local service: unavailable"
  };
  var FEED_STATUS_TEXT = {
    connecting: "Host feed: connecting",
    connected: "Host feed: connected",
    reconnecting: "Host feed: reconnecting",
    unavailable: "Host feed: unavailable"
  };

  var container = document.getElementById("timeline");
  var newMessagesButton = document.getElementById("new-messages");
  var clearButton = document.getElementById("clear");
  var skipButton = document.getElementById("skip");
  var topCard = document.getElementById("top-card");
  var topTier = document.getElementById("top-tier");
  var topAmount = document.getElementById("top-amount");
  var topUser = document.getElementById("top-user");
  var topText = document.getElementById("top-text");
  var topPending = document.getElementById("top-pending");
  var topRemaining = document.getElementById("top-remaining");
  var serviceStatusItem = document.getElementById("service-status");
  var serviceStatusLabel = document.getElementById("service-status-label");
  var feedStatusItem = document.getElementById("feed-status");
  var feedStatusLabel = document.getElementById("feed-status-label");
  var knownIds = new Set();
  var socket = null;
  var reconnectAttempt = 0;
  var reconnectTimer = null;
  var paused = false;
  var unread = 0;

  // View-local top-presentation state. ``pendingQueue`` is a deterministic FIFO
  // of received Super Chat messages waiting for their turn, and ``activeCard``
  // holds the single displayed top card plus its deterministic display start.
  var pendingQueue = [];
  var activeCard = null;
  var tickTimer = null;
  var serviceUp = null;

  function wsUrl() {
    var port = window.location.port || "17391";
    return "ws://127.0.0.1:" + port + "/ws/host";
  }

  function healthUrl() {
    var port = window.location.port || "17391";
    return "http://127.0.0.1:" + port + "/health";
  }

  function connect() {
    if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
      return;
    }
    socket = new WebSocket(wsUrl());
    socket.addEventListener("open", onOpen);
    socket.addEventListener("message", onMessage);
    socket.addEventListener("close", onClose);
    socket.addEventListener("error", onError);
    renderStatus();
  }

  function onOpen() {
    socket.send(HELLO_FRAME);
    renderStatus();
  }

  function onError() {
    // Connection errors are silent: the timeline simply stays put. The close
    // event that follows drives the reconnecting status, with no error details
    // surfaced to the user.
  }

  function onClose() {
    socket = null;
    renderStatus();
    scheduleReconnect();
  }

  function scheduleReconnect() {
    if (reconnectTimer) {
      return;
    }
    var index = Math.min(reconnectAttempt, RECONNECT_DELAYS_MS.length - 1);
    var delay = RECONNECT_DELAYS_MS[index];
    reconnectAttempt += 1;
    reconnectTimer = window.setTimeout(function () {
      reconnectTimer = null;
      connect();
    }, delay);
  }

  function probeHealth() {
    fetch(healthUrl())
      .then(function (response) {
        if (!response.ok) {
          return false;
        }
        return response.json().then(function (data) {
          return !!(data && data.status === "ok");
        });
      })
      .catch(function () {
        return false;
      })
      .then(function (ok) {
        serviceUp = ok;
        renderStatus();
        window.setTimeout(probeHealth, HEALTH_PROBE_INTERVAL_MS);
      });
  }

  function renderServiceStatus() {
    if (serviceUp === null) {
      return;
    }
    var state = serviceUp ? "available" : "unavailable";
    serviceStatusItem.className = "status__item status__item--" + state;
    serviceStatusLabel.textContent = SERVICE_STATUS_TEXT[state];
  }

  function renderFeedStatus() {
    var state;
    if (serviceUp === false) {
      state = "unavailable";
    } else if (socket && socket.readyState === WebSocket.OPEN) {
      state = "connected";
    } else if (socket && socket.readyState === WebSocket.CONNECTING) {
      state = "connecting";
    } else {
      state = "reconnecting";
    }
    feedStatusItem.className = "status__item status__item--" + state;
    feedStatusLabel.textContent = FEED_STATUS_TEXT[state];
  }

  function renderStatus() {
    renderServiceStatus();
    renderFeedStatus();
  }

  function onMessage(event) {
    var frame;
    try {
      frame = JSON.parse(event.data);
    } catch (_) {
      return;
    }
    if (!frame || frame.protocolVersion !== 1 || typeof frame !== "object") {
      return;
    }
    if (frame.type === "snapshot") {
      applySnapshot(frame.payload && frame.payload.messages);
    } else if (frame.type === "message.created") {
      appendMessage(frame.payload && frame.payload.message, true);
    }
  }

  function isNearBottom() {
    return (
      container.scrollHeight - container.scrollTop - container.clientHeight <
      SCROLL_SLACK_PX
    );
  }

  function scrollToBottom() {
    container.scrollTop = container.scrollHeight;
  }

  function applySnapshot(messages) {
    reconnectAttempt = 0;
    resetFollowState();
    clearList();
    resetPresentation();
    if (!Array.isArray(messages)) {
      return;
    }
    for (var i = 0; i < messages.length; i += 1) {
      appendMessage(messages[i], false);
    }
    advancePresentation(Date.now());
    renderTopCard(false);
    scrollToBottom();
  }

  function clearList() {
    knownIds.clear();
    while (container.firstChild) {
      container.removeChild(container.firstChild);
    }
  }

  function appendMessage(message, animate) {
    if (!message || typeof message.id !== "string") {
      return;
    }
    if (knownIds.has(message.id)) {
      return;
    }
    var element = renderMessage(message);
    if (!element) {
      return;
    }
    knownIds.add(message.id);
    element.dataset.messageId = message.id;
    if (animate) {
      element.classList.add("item--enter");
    }
    container.appendChild(element);
    while (container.childElementCount > MAX_ITEMS) {
      var oldest = container.firstElementChild;
      knownIds.delete(oldest.dataset.messageId);
      container.removeChild(oldest);
    }
    if (message.kind === "superChat") {
      pendingQueue.push(message);
    }
    if (animate) {
      if (paused) {
        unread += 1;
        showNewMessages();
      } else {
        scrollToBottom();
      }
    }
  }

  function showNewMessages() {
    newMessagesButton.textContent =
      unread + (unread === 1 ? " new message" : " new messages");
    newMessagesButton.hidden = false;
  }

  function hideNewMessages() {
    newMessagesButton.hidden = true;
  }

  function resetFollowState() {
    unread = 0;
    paused = false;
    hideNewMessages();
  }

  function returnToLatest() {
    resetFollowState();
    scrollToBottom();
  }

  function clearView() {
    clearList();
    resetFollowState();
    scrollToBottom();
  }

  function resetPresentation() {
    pendingQueue.length = 0;
    activeCard = null;
    renderTopCard(false);
  }

  function receivedAtMillis(value) {
    var parsed = Date.parse(value);
    return isNaN(parsed) ? 0 : parsed;
  }

  function advancePresentation(now) {
    var progressed = false;
    while (true) {
      var stepped = false;
      if (activeCard) {
        var expiryMs =
          activeCard.displayedAtMs +
          activeCard.message.data.durationSeconds * 1000;
        if (expiryMs <= now) {
          activeCard = null;
          stepped = true;
        }
      }
      if (!activeCard && pendingQueue.length > 0) {
        var front = pendingQueue[0];
        var dueMs = receivedAtMillis(front.receivedAt) + PRESENTATION_INTERVAL_MS;
        if (dueMs <= now) {
          pendingQueue.shift();
          activeCard = { message: front, displayedAtMs: dueMs };
          stepped = true;
        }
      }
      if (!stepped) {
        break;
      }
      progressed = true;
    }
    return progressed;
  }

  function superChatTier(amountMilliCny) {
    for (var i = 0; i < SUPER_CHAT_TIERS.length; i += 1) {
      if (amountMilliCny >= SUPER_CHAT_TIERS[i].min) {
        return SUPER_CHAT_TIERS[i];
      }
    }
    return SUPER_CHAT_TIERS[SUPER_CHAT_TIERS.length - 1];
  }

  function updatePendingCount() {
    topPending.textContent = pendingQueue.length + " pending";
  }

  function formatDuration(seconds) {
    var hours = Math.floor(seconds / 3600);
    var minutes = Math.floor((seconds % 3600) / 60);
    var secs = seconds % 60;
    if (hours > 0) {
      return hours + "h " + minutes + "m " + secs + "s";
    }
    if (minutes > 0) {
      return minutes + "m " + secs + "s";
    }
    return secs + "s";
  }

  function updateRemainingTime() {
    if (!activeCard) {
      return;
    }
    var remainingMs =
      activeCard.displayedAtMs +
      activeCard.message.data.durationSeconds * 1000 -
      Date.now();
    var seconds = Math.max(0, Math.ceil(remainingMs / 1000));
    topRemaining.textContent = formatDuration(seconds) + " remaining";
  }

  function renderTopCard(animate) {
    if (!activeCard) {
      topCard.hidden = true;
      topCard.className = "top-card";
      topTier.textContent = "";
      topAmount.textContent = "";
      topUser.textContent = "";
      topText.textContent = "";
      topPending.textContent = "";
      topRemaining.textContent = "";
      return;
    }
    var message = activeCard.message;
    var tier = superChatTier(message.data.amountMilliCny);
    topCard.hidden = false;
    topCard.className = "top-card top-card--" + tier.css;
    topTier.textContent = tier.label;
    topAmount.textContent = formatMoney(message.data.amountMilliCny);
    topUser.textContent = message.user.name;
    topText.textContent = message.data.text;
    updatePendingCount();
    updateRemainingTime();
    if (animate) {
      topCard.classList.add("top-card--enter");
      window.setTimeout(function () {
        topCard.classList.remove("top-card--enter");
      }, 200);
    }
  }

  function skipPresentation() {
    var now = Date.now();
    if (activeCard) {
      activeCard = null;
    }
    if (pendingQueue.length > 0) {
      var front = pendingQueue.shift();
      activeCard = { message: front, displayedAtMs: now };
    }
    renderTopCard(true);
  }

  function tickPresentation() {
    if (advancePresentation(Date.now())) {
      renderTopCard(true);
    } else if (activeCard) {
      updatePendingCount();
      updateRemainingTime();
    }
  }

  function startPresentationTicker() {
    if (tickTimer) {
      return;
    }
    tickTimer = window.setInterval(tickPresentation, TICK_INTERVAL_MS);
  }

  container.addEventListener("scroll", function () {
    paused = !isNearBottom();
    if (!paused) {
      unread = 0;
      hideNewMessages();
    }
  });

  newMessagesButton.addEventListener("click", returnToLatest);
  clearButton.addEventListener("click", clearView);
  skipButton.addEventListener("click", skipPresentation);

  function renderMessage(message) {
    var item = document.createElement("div");
    item.className = "item";
    var rendered = null;
    switch (message.kind) {
      case "danmaku":
        rendered = renderDanmaku(item, message);
        break;
      case "gift":
        rendered = renderGift(item, message);
        break;
      case "guard":
        rendered = renderGuard(item, message);
        break;
      case "superChat":
        rendered = renderSuperChat(item, message);
        break;
      default:
        return null;
    }
    if (!rendered) {
      return null;
    }
    appendActions(rendered, message);
    return rendered;
  }

  function renderDanmaku(item, message) {
    item.classList.add("item--danmaku");
    item.appendChild(textSpan("item__user", message.user.name));
    item.appendChild(textSpan("item__text", message.data.text));
    return item;
  }

  function renderGift(item, message) {
    item.classList.add("item--gift");
    item.appendChild(textSpan("item__label", "GIFT"));
    var body =
      message.user.name +
      " sent " +
      message.data.giftName +
      " \u00d7" +
      message.data.quantity +
      " \u00b7 " +
      formatMoney(message.data.totalAmountMilliCny);
    item.appendChild(textSpan("item__body", body));
    return item;
  }

  function renderGuard(item, message) {
    item.classList.add("item--guard");
    item.appendChild(textSpan("item__label", String(message.data.tier).toUpperCase()));
    var months = message.data.months;
    var body = message.user.name + " \u00b7 " + months + (months === 1 ? " month" : " months");
    item.appendChild(textSpan("item__body", body));
    return item;
  }

  function renderSuperChat(item, message) {
    item.classList.add("item--super-chat");
    item.appendChild(textSpan("item__label", "SC " + formatMoney(message.data.amountMilliCny)));
    item.appendChild(textSpan("item__body", message.user.name + ": " + message.data.text));
    return item;
  }

  function textSpan(className, text) {
    var span = document.createElement("span");
    span.className = className;
    span.textContent = text;
    return span;
  }

  // --- Host context actions ------------------------------------------------
  // Each timeline item exposes two accessible, view-local actions that persist
  // the represented user's existing OBS deny-list entry (stable ``user.id`` for
  // ``denyUserIds`` and the normalized ``user.name`` for ``denyNicknames``)
  // through the loopback ``/host/deny`` route. The server merges, deduplicates,
  // validates, and atomically persists the full configuration; the running
  // policy and canonical Host timeline are never changed, so a save reports
  // restart-required. Every label and message is rendered with DOM text APIs
  // only and no protocol frame is sent.
  var actionFeedback = document.getElementById("action-feedback");

  function denyUrl() {
    var port = window.location.port || "17391";
    return "http://127.0.0.1:" + port + "/host/deny";
  }

  function setActionFeedback(message, state) {
    actionFeedback.textContent = message;
    actionFeedback.className =
      "action-feedback" + (state ? " action-feedback--" + state : "");
    actionFeedback.hidden = !message;
  }

  function submitDeny(list, value) {
    setActionFeedback("Saving block...");
    fetch(denyUrl(), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ list: list, value: value })
    })
      .then(function (response) {
        return response.json().then(function (data) {
          return { ok: response.ok, data: data };
        });
      })
      .then(function (result) {
        if (result.ok && result.data && result.data.saved) {
          setActionFeedback("Blocked. Restart required to apply changes.", "ok");
        } else {
          var message =
            result.data && result.data.error
              ? result.data.error
              : "Could not save block.";
          setActionFeedback(message, "error");
        }
      })
      .catch(function () {
        setActionFeedback("Could not save block.", "error");
      });
  }

  function actionButton(label, list, value) {
    var button = document.createElement("button");
    button.type = "button";
    button.className = "item__action";
    button.textContent = label;
    button.addEventListener("click", function () {
      submitDeny(list, value);
    });
    return button;
  }

  function appendActions(item, message) {
    var actions = document.createElement("div");
    actions.className = "item__actions";
    actions.appendChild(
      actionButton("Block user", "denyUserIds", message.user.id)
    );
    actions.appendChild(
      actionButton("Block nickname", "denyNicknames", message.user.name)
    );
    if (messageText(message) != null) {
      actions.appendChild(copyTextButton(message));
    }
    actions.appendChild(copyUsernameButton(message));
    actions.appendChild(detailsButton(message));
    item.appendChild(actions);
  }

  function formatMoney(milliCny) {
    var cents = Math.round(milliCny / 10);
    var yuan = Math.floor(cents / 100);
    var remainder = cents % 100;
    var fraction = remainder < 10 ? "0" + remainder : String(remainder);
    return "\u00a5" + yuan + "." + fraction;
  }

  // --- Host message copy and details actions --------------------------------
  // Each Host timeline item also exposes three accessible, view-local
  // affordances that never touch the network, the canonical Host state, OBS
  // delivery, filtering, protocol v1, or the deny-list actions: copy the
  // message text (only kinds carrying the canonical ``data.text``), copy the
  // username (``user.name``), and show a concise details read-out built only
  // from existing canonical fields. Copy goes through the browser clipboard
  // seam; every label, value, and feedback message uses DOM text APIs only, and
  // fixed success/failure strings never surface clipboard exceptions or user
  // content.
  var detailsPanel = document.getElementById("details-panel");
  var detailsFields = document.getElementById("details-fields");
  var detailsClose = document.getElementById("details-close");

  function copyViaClipboard(text, successMessage, failureMessage) {
    if (!navigator.clipboard || typeof navigator.clipboard.writeText !== "function") {
      setActionFeedback("Copy is not available in this browser.", "error");
      return;
    }
    navigator.clipboard.writeText(text).then(function () {
      setActionFeedback(successMessage, "ok");
    }).catch(function () {
      setActionFeedback(failureMessage, "error");
    });
  }

  function messageText(message) {
    if (message.kind === "danmaku" || message.kind === "superChat") {
      return message.data.text;
    }
    return null;
  }

  function copyMessageText(message) {
    copyViaClipboard(
      messageText(message),
      "Message text copied.",
      "Could not copy the message text."
    );
  }

  function copyUsername(message) {
    copyViaClipboard(
      message.user.name,
      "Username copied.",
      "Could not copy the username."
    );
  }

  function copyTextButton(message) {
    var button = document.createElement("button");
    button.type = "button";
    button.className = "item__action";
    button.textContent = "Copy text";
    button.addEventListener("click", function () {
      copyMessageText(message);
    });
    return button;
  }

  function copyUsernameButton(message) {
    var button = document.createElement("button");
    button.type = "button";
    button.className = "item__action";
    button.textContent = "Copy username";
    button.addEventListener("click", function () {
      copyUsername(message);
    });
    return button;
  }

  function detailsButton(message) {
    var button = document.createElement("button");
    button.type = "button";
    button.className = "item__action";
    button.textContent = "Details";
    button.addEventListener("click", function () {
      openDetails(message);
    });
    return button;
  }

  function detailsRows(message) {
    var rows = [
      ["Kind", message.kind],
      ["User ID", message.user.id],
      ["Username", message.user.name],
      ["Received", message.receivedAt]
    ];
    switch (message.kind) {
      case "danmaku":
        rows.push(["Text", message.data.text]);
        break;
      case "gift":
        rows.push(["Gift", message.data.giftName]);
        rows.push(["Quantity", String(message.data.quantity)]);
        rows.push(["Total amount", formatMoney(message.data.totalAmountMilliCny)]);
        break;
      case "guard":
        rows.push(["Tier", String(message.data.tier)]);
        rows.push(["Months", String(message.data.months)]);
        break;
      case "superChat":
        rows.push(["Text", message.data.text]);
        rows.push(["Amount", formatMoney(message.data.amountMilliCny)]);
        rows.push(["Duration", formatDuration(message.data.durationSeconds)]);
        break;
      default:
        break;
    }
    return rows;
  }

  function clearDetails() {
    while (detailsFields.firstChild) {
      detailsFields.removeChild(detailsFields.firstChild);
    }
  }

  function openDetails(message) {
    clearDetails();
    var rows = detailsRows(message);
    for (var i = 0; i < rows.length; i += 1) {
      var term = document.createElement("dt");
      term.className = "details__term";
      term.textContent = rows[i][0];
      var desc = document.createElement("dd");
      desc.className = "details__desc";
      desc.textContent = rows[i][1];
      detailsFields.appendChild(term);
      detailsFields.appendChild(desc);
    }
    detailsPanel.hidden = false;
  }

  function closeDetails() {
    detailsPanel.hidden = true;
    clearDetails();
  }

  detailsClose.addEventListener("click", closeDetails);

  // --- Host settings surface -------------------------------------------------
  // A narrow, loopback-only settings panel for the existing non-secret
  // version-1 configuration. It reads the current config from the local
  // ``/host/settings`` route, exposes only the editable fields (port, mock
  // cadence, the three deny lists, and the gift threshold) while keeping
  // ``service.host`` and ``snapshot.maxMessages`` fixed, and submits a full
  // candidate back through the same route. Every value and message is rendered
  // with DOM text APIs only; no secrets are ever read, shown, or sent.
  var settingsToggle = document.getElementById("settings-toggle");
  var settingsPanel = document.getElementById("settings-panel");
  var settingsForm = document.getElementById("settings-form");
  var settingsClose = document.getElementById("settings-close");
  var settingsFeedback = document.getElementById("settings-feedback");
  var settingsHost = document.getElementById("settings-host");
  var settingsPort = document.getElementById("settings-port");
  var settingsCadence = document.getElementById("settings-cadence");
  var settingsMaxMessages = document.getElementById("settings-max-messages");
  var settingsDenyUserIds = document.getElementById("settings-deny-user-ids");
  var settingsDenyNicknames = document.getElementById("settings-deny-nicknames");
  var settingsKeywords = document.getElementById("settings-keywords");
  var settingsGiftThreshold = document.getElementById("settings-gift-threshold");

  function settingsUrl() {
    var port = window.location.port || "17391";
    return "http://127.0.0.1:" + port + "/host/settings";
  }

  function setSettingsFeedback(message, state) {
    settingsFeedback.textContent = message;
    settingsFeedback.className = "settings__feedback" +
      (state ? " settings__feedback--" + state : "");
  }

  function numberValue(text) {
    var parsed = parseInt(text, 10);
    return isNaN(parsed) ? null : parsed;
  }

  function listLines(text) {
    return text
      .split("\n")
      .map(function (line) { return line.trim(); })
      .filter(function (line) { return line.length > 0; });
  }

  function populateSettings(config) {
    var service = config.service || {};
    var mock = config.mock || {};
    var snapshot = config.snapshot || {};
    var obs = config.obs || {};
    settingsHost.value = service.host || "";
    settingsPort.value = service.port == null ? "" : String(service.port);
    settingsCadence.value =
      mock.cadenceMilliseconds == null ? "" : String(mock.cadenceMilliseconds);
    settingsMaxMessages.value =
      snapshot.maxMessages == null ? "" : String(snapshot.maxMessages);
    settingsDenyUserIds.value = (obs.denyUserIds || []).join("\n");
    settingsDenyNicknames.value = (obs.denyNicknames || []).join("\n");
    settingsKeywords.value = (obs.keywords || []).join("\n");
    settingsGiftThreshold.value =
      obs.giftThresholdMilliCny == null ? "" : String(obs.giftThresholdMilliCny);
  }

  function buildCandidate() {
    return {
      configVersion: 1,
      service: { host: "127.0.0.1", port: numberValue(settingsPort.value) },
      mock: { cadenceMilliseconds: numberValue(settingsCadence.value) },
      snapshot: { maxMessages: 100 },
      obs: {
        denyUserIds: listLines(settingsDenyUserIds.value),
        denyNicknames: listLines(settingsDenyNicknames.value),
        keywords: listLines(settingsKeywords.value),
        giftThresholdMilliCny: numberValue(settingsGiftThreshold.value)
      }
    };
  }

  function loadSettings() {
    setSettingsFeedback("Loading settings...");
    fetch(settingsUrl())
      .then(function (response) { return response.json(); })
      .then(function (data) {
        if (data && data.config) {
          populateSettings(data.config);
          setSettingsFeedback("");
        } else {
          setSettingsFeedback("Could not read settings.", "error");
        }
      })
      .catch(function () {
        setSettingsFeedback("Could not read settings.", "error");
      });
  }

  function saveSettings(event) {
    event.preventDefault();
    setSettingsFeedback("Saving...");
    fetch(settingsUrl(), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildCandidate())
    })
      .then(function (response) {
        return response.json().then(function (data) {
          return { ok: response.ok, data: data };
        });
      })
      .then(function (result) {
        if (result.ok && result.data && result.data.saved) {
          setSettingsFeedback("Saved. Restart required to apply changes.", "ok");
        } else {
          var message =
            result.data && result.data.error
              ? result.data.error
              : "Could not save settings.";
          setSettingsFeedback(message, "error");
        }
      })
      .catch(function () {
        setSettingsFeedback("Could not save settings.", "error");
      });
  }

  function openSettings() {
    settingsPanel.hidden = false;
    settingsToggle.setAttribute("aria-expanded", "true");
    loadSettings();
  }

  function closeSettings() {
    settingsPanel.hidden = true;
    settingsToggle.setAttribute("aria-expanded", "false");
  }

  function toggleSettings() {
    if (settingsPanel.hidden) {
      openSettings();
    } else {
      closeSettings();
    }
  }

  settingsToggle.addEventListener("click", toggleSettings);
  settingsClose.addEventListener("click", closeSettings);
  settingsForm.addEventListener("submit", saveSettings);

  // --- Host OBS setup --------------------------------------------------------
  // A loopback-only setup surface that shows the existing OBS overlay URL — the
  // current local service origin (fixed 127.0.0.1 plus the served port) with the
  // /obs route appended — and copies it through the browser clipboard seam. It
  // changes no OBS protocol, route, filtering, or Host timeline semantics. Every
  // value and message is rendered with DOM text/value APIs only, and fixed
  // success/failure feedback never surfaces clipboard exceptions or user content.
  var obsToggle = document.getElementById("obs-toggle");
  var obsPanel = document.getElementById("obs-panel");
  var obsClose = document.getElementById("obs-close");
  var obsCopyButton = document.getElementById("obs-copy");
  var obsUrlInput = document.getElementById("obs-url");
  var obsFeedback = document.getElementById("obs-feedback");

  function obsUrl() {
    var port = window.location.port || "17391";
    return "http://127.0.0.1:" + port + "/obs";
  }

  function setObsFeedback(message, state) {
    obsFeedback.textContent = message;
    obsFeedback.className =
      "settings__feedback" + (state ? " settings__feedback--" + state : "");
  }

  function populateObsUrl() {
    obsUrlInput.value = obsUrl();
  }

  function copyObsUrl() {
    if (!navigator.clipboard || typeof navigator.clipboard.writeText !== "function") {
      setObsFeedback("Copy is not available in this browser.", "error");
      return;
    }
    navigator.clipboard.writeText(obsUrl()).then(function () {
      setObsFeedback("OBS URL copied.", "ok");
    }).catch(function () {
      setObsFeedback("Could not copy the OBS URL.", "error");
    });
  }

  // --- OBS message preview ---------------------------------------------------
  // A deterministic, local-only preview of the four canonical OBS message kinds
  // (danmaku, gift, guard, Super Chat). The samples are hardcoded: they are
  // never read from the Host timeline, the running configuration, or Bilibili,
  // and they are never sent to the core, the OBS WebSocket, or any network.
  // Each item reuses the exact same kind renderers and presentation classes as
  // the OBS overlay, but renders no action buttons, so the preview is inert and
  // changes no runtime, filtering, or persistence state.
  var previewList = document.getElementById("preview-list");

  var PREVIEW_MESSAGES = [
    {
      kind: "danmaku",
      user: { id: "preview:danmaku", name: "Sample" },
      data: { text: "Hello from the preview!" }
    },
    {
      kind: "gift",
      user: { id: "preview:gift", name: "Sample" },
      data: { giftName: "Star", quantity: 5, totalAmountMilliCny: 5000 }
    },
    {
      kind: "guard",
      user: { id: "preview:guard", name: "Sample" },
      data: { tier: "captain", months: 3 }
    },
    {
      kind: "superChat",
      user: { id: "preview:super-chat", name: "Sample" },
      data: { text: "Thanks for watching!", amountMilliCny: 30000, durationSeconds: 60 }
    }
  ];

  function renderPreviewItem(message) {
    var item = document.createElement("div");
    item.className = "item";
    switch (message.kind) {
      case "danmaku":
        return renderDanmaku(item, message);
      case "gift":
        return renderGift(item, message);
      case "guard":
        return renderGuard(item, message);
      case "superChat":
        return renderSuperChat(item, message);
      default:
        return null;
    }
  }

  function clearPreview() {
    while (previewList.firstChild) {
      previewList.removeChild(previewList.firstChild);
    }
  }

  function renderPreview() {
    clearPreview();
    for (var i = 0; i < PREVIEW_MESSAGES.length; i += 1) {
      var item = renderPreviewItem(PREVIEW_MESSAGES[i]);
      if (item) {
        item.classList.add("preview__item");
        previewList.appendChild(item);
      }
    }
  }

  function openObsSetup() {
    obsPanel.hidden = false;
    obsToggle.setAttribute("aria-expanded", "true");
    populateObsUrl();
    renderPreview();
  }

  function closeObsSetup() {
    obsPanel.hidden = true;
    obsToggle.setAttribute("aria-expanded", "false");
  }

  function toggleObsSetup() {
    if (obsPanel.hidden) {
      openObsSetup();
    } else {
      closeObsSetup();
    }
  }

  obsToggle.addEventListener("click", toggleObsSetup);
  obsClose.addEventListener("click", closeObsSetup);
  obsCopyButton.addEventListener("click", copyObsUrl);

  startPresentationTicker();
  connect();
  probeHealth();
})();
