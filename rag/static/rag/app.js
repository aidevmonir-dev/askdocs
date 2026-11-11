(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var docsEl = $("docs"), thread = $("thread"), empty = $("empty");
  var fileInput = $("file"), uploadForm = $("upload"), uploadStatus = $("upload-status");
  var askForm = $("ask"), questionEl = $("question"), sendBtn = $("send");
  var history = [];

  function csrf() {
    var m = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }

  function api(url, options) {
    options = options || {};
    options.headers = Object.assign({ "X-CSRFToken": csrf() }, options.headers || {});
    options.credentials = "same-origin";
    return fetch(url, options).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (body) {
        if (!res.ok) throw new Error(body.error || "Something went wrong. Try again.");
        return body;
      });
    });
  }

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  // ---- documents ---------------------------------------------------------------------------
  function renderDocs(list) {
    docsEl.textContent = "";
    list.forEach(function (doc) {
      var li = el("li");
      li.appendChild(el("span", "doc-name", doc.name));
      li.appendChild(el("span", "doc-meta", doc.pages + (doc.pages === 1 ? " page" : " pages")));
      if (!doc.sample) {
        var remove = el("button", "", "Remove");
        remove.type = "button";
        remove.setAttribute("aria-label", "Remove " + doc.name);
        remove.addEventListener("click", function () {
          api("/api/documents/" + doc.id + "/delete", { method: "POST" }).then(loadDocs).catch(showUploadError);
        });
        li.appendChild(remove);
      }
      docsEl.appendChild(li);
    });
  }

  function loadDocs() {
    return api("/api/documents").then(function (body) { renderDocs(body.documents); });
  }

  function setUploadStatus(text, kind) {
    uploadStatus.textContent = text;
    uploadStatus.className = "status" + (kind ? " " + kind : "");
  }
  function showUploadError(err) { setUploadStatus(err.message, "bad"); }

  function upload(file) {
    if (!file) return;
    var data = new FormData();
    data.append("file", file);
    setUploadStatus("Reading " + file.name + "...", "");
    api("/api/documents", { method: "POST", body: data })
      .then(function (body) {
        setUploadStatus(body.document.name + " is ready. Ask a question about it.", "good");
        return loadDocs();
      })
      .catch(showUploadError)
      .then(function () { fileInput.value = ""; });
  }

  $("pick").addEventListener("click", function () { fileInput.click(); });
  fileInput.addEventListener("change", function () { upload(fileInput.files[0]); });
  ["dragenter", "dragover"].forEach(function (name) {
    uploadForm.addEventListener(name, function (e) { e.preventDefault(); uploadForm.classList.add("over"); });
  });
  ["dragleave", "drop"].forEach(function (name) {
    uploadForm.addEventListener(name, function (e) { e.preventDefault(); uploadForm.classList.remove("over"); });
  });
  uploadForm.addEventListener("drop", function (e) { upload(e.dataTransfer.files[0]); });

  // ---- chat --------------------------------------------------------------------------------
  function toggleSource(li, open) {
    var quote = li.querySelector(".src-quote"), head = li.querySelector(".src-head");
    var show = open === undefined ? quote.hidden : open;
    quote.hidden = !show;
    head.setAttribute("aria-expanded", String(show));
  }

  function renderAnswer(box, data) {
    box.textContent = "";
    var p = el("p");
    var items = {};
    data.segments.forEach(function (seg) {
      p.appendChild(document.createTextNode(seg.text));
      (seg.sources || []).forEach(function (n) {
        var mark = el("button", "mark", String(n));
        mark.type = "button";
        mark.setAttribute("aria-label", "Show source " + n);
        mark.addEventListener("click", function () {
          if (!items[n]) return;
          toggleSource(items[n], true);
          items[n].scrollIntoView({ block: "nearest", behavior: "smooth" });
        });
        p.appendChild(mark);
      });
    });
    box.appendChild(p);
    if (data.notice) box.appendChild(el("p", "notice", data.notice));
    if (data.sources && data.sources.length) {
      var list = el("ul", "sources");
      data.sources.forEach(function (src) {
        var li = el("li");
        var head = el("button", "src-head");
        head.type = "button";
        head.appendChild(el("span", "src-num", String(src.number)));
        head.appendChild(el("span", "src-where", src.document + ", page " + src.page));
        var quote = el("p", "src-quote", src.quote);
        quote.hidden = data.mode !== "retrieval_only";
        head.setAttribute("aria-expanded", String(!quote.hidden));
        head.addEventListener("click", function () { toggleSource(li); });
        li.appendChild(head);
        li.appendChild(quote);
        list.appendChild(li);
        items[src.number] = li;
      });
      box.appendChild(list);
    }
  }

  function ask(question) {
    question = question.trim();
    if (!question || sendBtn.disabled) return;
    if (empty) { empty.remove(); empty = null; }
    var turn = el("div", "turn");
    turn.appendChild(el("p", "q", question));
    var box = el("div", "a");
    box.appendChild(el("p", "thinking", "Searching your documents"));
    turn.appendChild(box);
    thread.appendChild(turn);
    turn.scrollIntoView({ block: "start", behavior: "smooth" });

    sendBtn.disabled = true;
    questionEl.value = "";
    questionEl.style.height = "auto";
    api("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: question, history: history }),
    })
      .then(function (data) {
        renderAnswer(box, data);
        if (data.mode === "ai") history.push({ question: question, answer: data.text });
      })
      .catch(function (err) {
        box.textContent = "";
        box.appendChild(el("p", "", err.message));
      })
      .then(function () { sendBtn.disabled = false; questionEl.focus(); });
  }

  askForm.addEventListener("submit", function (e) { e.preventDefault(); ask(questionEl.value); });
  questionEl.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(questionEl.value); }
  });
  questionEl.addEventListener("input", function () {
    questionEl.style.height = "auto";
    questionEl.style.height = questionEl.scrollHeight + "px";
  });
  document.querySelectorAll(".starter").forEach(function (btn) {
    btn.addEventListener("click", function () { ask(btn.textContent); });
  });

  loadDocs().catch(function () {});
})();
