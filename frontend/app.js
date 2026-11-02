const form = document.querySelector("#query-form");
const question = document.querySelector("#question");
const submit = document.querySelector("#submit-btn");
const answerSection = document.querySelector("#answer-section");
const chat = document.querySelector("#chat");
const charCount = document.querySelector("#char-count");
const clearButton = document.querySelector("#clear-btn");
const content = document.querySelector(".content");
const databaseStatus = document.querySelector("#database-status");
const databaseStatusDot = document.querySelector("#database-status-dot");
const sessionKey = "skillquery-session-id";

fetch("/health")
  .then((response) => {
    if (!response.ok) throw new Error("Database unavailable");
    return response.json();
  })
  .then((health) => {
    databaseStatus.textContent = health.database === "connected" ? "Database connected" : "Database unavailable";
    databaseStatusDot.classList.toggle("disconnected", health.database !== "connected");
  })
  .catch(() => {
    databaseStatus.textContent = "Database unavailable";
    databaseStatusDot.classList.add("disconnected");
  });

let sessionId = localStorage.getItem(sessionKey);
let clarificationContext = null;
if (!sessionId) {
  sessionId = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
  localStorage.setItem(sessionKey, sessionId);
}

function addMessage(role, text, options = [], tableData = null) {
  const message = document.createElement("div");
  message.className = `chat-message ${role}`;
  if (role === "user") {
    message.textContent = text;
  } else {
    const avatar = document.createElement("div");
    avatar.className = "answer-avatar";
    avatar.textContent = "S";
    const body = document.createElement("div");
    body.className = "message-body";
    const label = document.createElement("p");
    label.className = "answer-label";
    label.textContent = "SkillQuery assistant";
    const content = document.createElement("div");
    content.className = "answer";
    const hasStudentTable = tableData && tableData.rows && tableData.rows.length
      && tableData.columns && tableData.columns.includes("name");
    if (!hasStudentTable) {
      renderAnswer(content, text, options.length > 0);
      body.append(label, content);
    }
    if (hasStudentTable) {
      body.appendChild(createTable(tableData.columns, tableData.rows));
    }
    if (options.length) {
      const optionList = document.createElement("div");
      optionList.className = "option-list";
      const optionSubmit = document.createElement("button");
      optionSubmit.className = "option-submit";
      optionSubmit.type = "button";
      optionSubmit.textContent = "Submit selection";
      optionSubmit.disabled = true;
      let selectedNumber = null;
      options.forEach(({number, label: optionLabel}) => {
        const option = document.createElement("button");
        option.className = "option-button";
        option.type = "button";
        option.innerHTML = `<span>${number}</span>${optionLabel}`;
        option.addEventListener("click", () => {
          selectedNumber = number;
          optionSubmit.disabled = false;
          optionList.querySelectorAll(".option-button").forEach((item) => item.classList.remove("selected"));
          option.classList.add("selected");
        });
        optionList.appendChild(option);
      });
      optionSubmit.addEventListener("click", () => {
        question.value = String(selectedNumber);
        question.dispatchEvent(new Event("input"));
        form.requestSubmit();
      });
      optionList.appendChild(optionSubmit);
      body.appendChild(optionList);
    }
    message.append(avatar, body);
  }
  chat.appendChild(message);
  message.scrollIntoView({behavior: "smooth", block: "nearest"});
}

function createTable(columns, rows) {
  const wrapper = document.createElement("div");
  wrapper.className = "table-wrap";
  const table = document.createElement("table");
  const head = document.createElement("thead");
  const headerRow = document.createElement("tr");
  columns.forEach((column) => {
    const cell = document.createElement("th");
    cell.textContent = column.replaceAll("_", " ");
    headerRow.appendChild(cell);
  });
  head.appendChild(headerRow);
  const body = document.createElement("tbody");
  rows.forEach((row) => {
    const tableRow = document.createElement("tr");
    columns.forEach((column) => {
      const cell = document.createElement("td");
      cell.textContent = row[column] ?? "";
      tableRow.appendChild(cell);
    });
    body.appendChild(tableRow);
  });
  table.append(head, body);
  const actions = document.createElement("div");
  actions.className = "table-actions";
  const csvButton = document.createElement("button");
  csvButton.className = "table-action";
  csvButton.textContent = "↓ Download CSV";
  csvButton.addEventListener("click", () => downloadCsv(columns, rows));
  const sheetsButton = document.createElement("button");
  sheetsButton.className = "table-action";
  sheetsButton.textContent = "▣ Copy for Google Sheets";
  sheetsButton.addEventListener("click", async () => {
    const tsv = [columns.join("\t"), ...rows.map((row) => columns.map((column) => row[column] ?? "").join("\t"))].join("\n");
    await navigator.clipboard.writeText(tsv);
    sheetsButton.textContent = "✓ Copied — paste into Google Sheets";
    setTimeout(() => { sheetsButton.textContent = "▣ Copy for Google Sheets"; }, 2500);
  });
  actions.append(csvButton, sheetsButton);
  wrapper.append(table, actions);
  return wrapper;
}

function downloadCsv(columns, rows) {
  const escape = (value) => `"${String(value ?? "").replaceAll('"', '""')}"`;
  const csv = [columns.map(escape).join(","), ...rows.map((row) => columns.map((column) => escape(row[column])).join(","))].join("\n");
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([csv], {type: "text/csv;charset=utf-8"}));
  link.download = "skillquery-student-results.csv";
  link.click();
  URL.revokeObjectURL(link.href);
}

function renderAnswer(element, text, hasOptions) {
  const optionLine = /^\s*\*?\d+\*?\s*[-.)]\s*.+$/;
  const lines = text.split("\n").filter((line) => !hasOptions || !optionLine.test(line));
  const cleaned = lines.join("\n").replace(/\n{3,}/g, "\n\n").trim();
  const parts = cleaned.split(/(\*\*[^*]+\*\*)/g);
  parts.forEach((part) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      const strong = document.createElement("strong");
      strong.textContent = part.slice(2, -2);
      element.appendChild(strong);
    } else {
      element.appendChild(document.createTextNode(part));
    }
  });
}

function extractOptions(text) {
  if (!/select|which|option|matching topic|level/i.test(text)) return [];
  return text.split("\n").flatMap((line) => {
    const match = line.match(/^\s*\*?(\d+)\*?\s*[-.)]\s*(.+?)\s*$/);
    return match ? [{number: Number(match[1]), label: match[2].replace(/\*/g, "").trim()}] : [];
  });
}

question.addEventListener("input", () => {
  charCount.textContent = `${question.value.length} / 1000`;
});

document.querySelectorAll(".suggestion").forEach((button) => {
  button.addEventListener("click", () => {
    question.value = button.dataset.question;
    question.dispatchEvent(new Event("input"));
    question.focus();
  });
});

question.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = question.value.trim();
  if (!text) return;
  addMessage("user", text);
  content.classList.add("has-chat");
  submit.disabled = true;
  submit.querySelector("span:first-child").textContent = "Analyzing…";
  answerSection.classList.remove("hidden");
  const loading = document.createElement("div");
  loading.className = "chat-message assistant loading";
  loading.textContent = "Searching your student records…";
  chat.appendChild(loading);
  try {
    const response = await fetch("/api/query", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({question: text, session_id: sessionId, clarification_context: clarificationContext}),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Request failed.");
    loading.remove();
    clarificationContext = data.clarification || null;
    addMessage("assistant", data.answer, extractOptions(data.answer), data);
    question.value = "";
    question.dispatchEvent(new Event("input"));
  } catch (error) {
    loading.remove();
    addMessage("assistant", error.message || "Something went wrong. Please try again.");
  } finally {
    submit.disabled = false;
    submit.querySelector("span:first-child").textContent = "Ask SkillQuery";
  }
});

clearButton.addEventListener("click", () => {
  chat.innerHTML = "";
  clarificationContext = null;
  content.classList.remove("has-chat");
  answerSection.classList.add("hidden");
});
