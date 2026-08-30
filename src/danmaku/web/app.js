(function () {
  "use strict";

  var MAX_ITEMS = 100;
  var RECONNECT_DELAYS_MS = [500, 1000, 2000, 4000, 5000];
  var HELLO_FRAME = '{"protocolVersion":1,"type":"hello","payload":{}}';

  var container = document.getElementById("messages");
  var knownIds = new Set();
  var socket = null;
  var reconnectAttempt = 0;
  var reconnectTimer = null;

  function wsUrl() {
    var port = window.location.port || "17391";
    return "ws://127.0.0.1:" + port + "/ws";
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
  }

  function onOpen() {
    socket.send(HELLO_FRAME);
  }

  function onError() {
    // Connection errors remain visually transparent: nothing is rendered.
  }

  function onClose() {
    socket = null;
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

  function applySnapshot(messages) {
    reconnectAttempt = 0;
    clearList();
    if (!Array.isArray(messages)) {
      return;
    }
    for (var i = 0; i < messages.length; i += 1) {
      appendMessage(messages[i], false);
    }
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
  }

  function renderMessage(message) {
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

  function formatMoney(milliCny) {
    var cents = Math.round(milliCny / 10);
    var yuan = Math.floor(cents / 100);
    var remainder = cents % 100;
    var fraction = remainder < 10 ? "0" + remainder : String(remainder);
    return "\u00a5" + yuan + "." + fraction;
  }

  connect();
})();
