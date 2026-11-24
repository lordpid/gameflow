"use strict";

const form = document.querySelector("#connect-form");
const subjectInput = document.querySelector("#subject");
const tokenInput = document.querySelector("#token");
const connectButton = document.querySelector("#connect-button");
const connectPanel = document.querySelector("#connect-panel");
const dashboard = document.querySelector("#dashboard");
const disconnectButton = document.querySelector("#disconnect");
const refreshButton = document.querySelector("#refresh");
const formError = document.querySelector("#form-error");
const dashboardError = document.querySelector("#dashboard-error");
const numberFormat = new Intl.NumberFormat("pl-PL");
let session = null;

function messageFor(error) {
  const messages = {
    invalid_token: "Token jest nieprawidłowy lub wygasł. Wygeneruj nowy token testowy.",
    identity_forbidden: "Token nie pasuje do podanego identyfikatora gracza.",
    identity_not_found: "Nie znaleziono tożsamości testowej o tym identyfikatorze.",
    profile_not_found: "Ta tożsamość nie ma jeszcze profilu.",
    auth_not_configured: "Uwierzytelnianie testowe nie jest skonfigurowane na serwerze.",
    not_configured: "Baza danych nie jest skonfigurowana na serwerze.",
  };
  return messages[error.message] || "Nie udało się pobrać profilu. Sprawdź połączenie i spróbuj ponownie.";
}

async function fetchModel(path, credentials) {
  const params = new URLSearchParams({ provider: "membersflow", subject: credentials.subject });
  const response = await fetch(`${path}?${params}`, {
    headers: { Authorization: `Bearer ${credentials.token}` },
    cache: "no-store",
  });
  if (!response.ok) {
    let body;
    try {
      body = await response.json();
    } catch (error) {
      throw new Error(`HTTP ${response.status}`, { cause: error });
    }
    throw new Error(body.error || `HTTP ${response.status}`);
  }
  return response.json();
}

function makeItem(icon, title, detail, amount) {
  const item = document.createElement("li");
  item.className = "item";
  const iconElement = document.createElement("span");
  iconElement.className = `item-icon${amount === undefined ? "" : " transaction-icon"}`;
  iconElement.setAttribute("aria-hidden", "true");
  iconElement.textContent = icon;
  const text = document.createElement("span");
  text.className = "item-text";
  const name = document.createElement("strong");
  name.textContent = title;
  text.append(name);
  if (detail) {
    const description = document.createElement("small");
    description.textContent = detail;
    text.append(description);
  }
  item.append(iconElement, text);
  if (amount !== undefined) {
    const value = document.createElement("span");
    value.className = `amount${amount < 0 ? " negative" : ""}`;
    value.textContent = `${amount > 0 ? "+" : ""}${numberFormat.format(amount)} XP`;
    item.append(value);
  }
  return item;
}

function render(profile, progression) {
  document.querySelector("#xp-value").textContent = numberFormat.format(profile.profile.xp);
  document.querySelector("#achievement-count").textContent = numberFormat.format(profile.achievements.length);
  document.querySelector("#identity-value").textContent = profile.identity.subject;
  const achievements = document.querySelector("#achievements");
  achievements.replaceChildren(...profile.achievements.map(
    (achievement) => makeItem("★", achievement.name, achievement.code),
  ));
  document.querySelector("#achievements-empty").hidden = profile.achievements.length !== 0;
  const transactions = document.querySelector("#transactions");
  transactions.replaceChildren(...progression.transactions.slice().reverse().map(
    (transaction) => makeItem("✦", transaction.source, "", transaction.xp),
  ));
  document.querySelector("#transactions-empty").hidden = progression.transactions.length !== 0;
}

async function load(credentials) {
  return Promise.all([
    fetchModel("/v1/profile", credentials),
    fetchModel("/v1/profile/progression", credentials),
  ]);
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const credentials = { subject: subjectInput.value.trim(), token: tokenInput.value.trim() };
  if (!credentials.subject || !credentials.token) {
    formError.textContent = "Podaj identyfikator gracza i token testowy.";
    formError.hidden = false;
    return;
  }
  formError.hidden = true;
  connectButton.disabled = true;
  connectButton.textContent = "Łączenie…";
  try {
    const [profile, progression] = await load(credentials);
    render(profile, progression);
    session = credentials;
    tokenInput.value = "";
    connectPanel.hidden = true;
    dashboard.hidden = false;
    disconnectButton.hidden = false;
  } catch (error) {
    formError.textContent = messageFor(error);
    formError.hidden = false;
  } finally {
    connectButton.disabled = false;
    connectButton.textContent = "Pokaż mój profil →";
  }
});

refreshButton.addEventListener("click", async () => {
  if (!session) return;
  const credentials = session;
  refreshButton.disabled = true;
  dashboardError.hidden = true;
  try {
    const [profile, progression] = await load(credentials);
    if (session === credentials) render(profile, progression);
  } catch (error) {
    if (session === credentials) {
      dashboardError.textContent = messageFor(error);
      dashboardError.hidden = false;
    }
  } finally {
    refreshButton.disabled = false;
  }
});

disconnectButton.addEventListener("click", () => {
  session = null;
  form.reset();
  formError.hidden = true;
  dashboardError.hidden = true;
  dashboard.hidden = true;
  disconnectButton.hidden = true;
  connectPanel.hidden = false;
  document.querySelector("#identity-value").textContent = "";
  document.querySelector("#xp-value").textContent = "0";
  document.querySelector("#achievement-count").textContent = "0";
  document.querySelector("#achievements").replaceChildren();
  document.querySelector("#transactions").replaceChildren();
  subjectInput.focus();
});
