let manualBoxes = []
let botBoxes = []
let isDropdownOpen = false

const STORAGE_KEY = "instantcash_manual_boxes_v1"
const OBS_MODE =
  new URLSearchParams(window.location.search).get("obs") === "1" ||
  /^\/obs\/?$/.test(window.location.pathname)

const timeOptions = []
for (let hour = 10; hour <= 22; hour++) {
  const displayHour = hour > 12 ? hour - 12 : hour
  const ampm = hour >= 12 ? "PM" : "AM"
  timeOptions.push(`${displayHour}:00 ${ampm}`)
  if (hour < 22) timeOptions.push(`${displayHour}:30 ${ampm}`)
}

const yesterdayDrawTimes = [
  "10:00 PM",
  "9:30 PM",
  "9:00 PM",
  "8:30 PM",
  "8:00 PM",
  "7:30 PM",
  "7:00 PM",
  "6:30 PM",
  "6:00 PM",
  "5:30 PM",
  "5:00 PM",
  "4:30 PM",
]

document.addEventListener("DOMContentLoaded", () => {
  if (OBS_MODE) document.body.classList.add("obs-mode")
  initializeTimeOptions()
  initializeEventListeners()

  manualBoxes = loadManualBoxes()

  updateDateTime()
  updateCountdown()
  renderNumberBoxes()
  loadLatest()

  setInterval(updateDateTime, 1000)
  setInterval(updateCountdown, 1000)
  setInterval(loadLatest, 15000)
})

function getNyNow() {
  return new Date(new Date().toLocaleString("en-US", { timeZone: "America/New_York" }))
}

function formatDateLabel(dateObj) {
  return dateObj.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  })
}

function parseTimeLabel(timeStr) {
  const m = /^(\d{1,2}):(\d{2})\s*(AM|PM)$/i.exec(String(timeStr || "").trim())
  if (!m) return null

  let hours = parseInt(m[1], 10)
  const minutes = parseInt(m[2], 10)
  const ampm = m[3].toUpperCase()

  if (ampm === "PM" && hours !== 12) hours += 12
  if (ampm === "AM" && hours === 12) hours = 0

  return { hours, minutes }
}

function makeSortTsForDay(timeStr, dayOffset = 0) {
  const d = getNyNow()
  d.setDate(d.getDate() + dayOffset)
  d.setHours(0, 0, 0, 0)

  const parsed = parseTimeLabel(timeStr)
  if (parsed) {
    d.setHours(parsed.hours, parsed.minutes, 0, 0)
  }

  return d.getTime()
}

function makeDateLabelForDay(dayOffset = 0) {
  const d = getNyNow()
  d.setDate(d.getDate() + dayOffset)
  return formatDateLabel(d)
}

function makeSlotKey(dateLabel, timeLabel, isYesterday = false) {
  return `${dateLabel}__${timeLabel}__${isYesterday ? "Y" : "T"}`
}

function digitsOnly(value) {
  return String(value || "").replace(/\D/g, "")
}

function toDigits(value) {
  return String(value || "")
    .replace(/\D/g, "")
    .split("")
    .map((n) => parseInt(n, 10))
    .filter((n) => Number.isInteger(n))
}

function sanitizeDigitsArray(arr) {
  if (!Array.isArray(arr)) return []
  return arr
    .map((n) => parseInt(n, 10))
    .filter((n) => Number.isInteger(n) && n >= 0 && n <= 9)
}

function sanitizeBox(box) {
  if (!box || typeof box !== "object") return null

  const date = String(box.date || "").trim()
  const time = String(box.time || "").trim()

  if (!date || !time) return null

  const isYesterday = !!box.isYesterday
  const pick2 = sanitizeDigitsArray(box.pick2)
  const pick3 = sanitizeDigitsArray(box.pick3)
  const pick4 = sanitizeDigitsArray(box.pick4)
  const pick5 = sanitizeDigitsArray(box.pick5)

  if (!pick2.length && !pick3.length && !pick4.length && !pick5.length) return null

  const slotKey = box.slotKey || makeSlotKey(date, time, isYesterday)
  const sortTs = Number.isFinite(box.sortTs) ? box.sortTs : makeSortTsForDay(time, isYesterday ? -1 : 0)

  return {
    id: String(box.id || `manual-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`),
    draw_id: String(box.draw_id || box.id || ""),
    date,
    time,
    pick2,
    pick3,
    pick4,
    pick5,
    isYesterday,
    sortTs,
    slotKey,
    source: box.source === "bot" ? "bot" : "manual",
  }
}

function loadManualBoxes() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return []

    const arr = JSON.parse(raw)
    if (!Array.isArray(arr)) return []

    return arr.map(sanitizeBox).filter(Boolean)
  } catch {
    return []
  }
}

function saveManualBoxes() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(manualBoxes))
  } catch {}
}

function initializeTimeOptions() {
  const select = document.getElementById("selectedTime")
  if (!select) return

  select.innerHTML = `<option value="">Choose a time...</option>`
  timeOptions.forEach((time) => {
    const option = document.createElement("option")
    option.value = time
    option.textContent = time
    select.appendChild(option)
  })
}

function initializeEventListeners() {
  document.getElementById("menuButton")?.addEventListener("click", toggleDropdown)
  document.getElementById("addNumbersBtn")?.addEventListener("click", handleAddNumbers)
  document.getElementById("yesterdayBtn")?.addEventListener("click", showYesterdayPopup)
  document.getElementById("resetBtn")?.addEventListener("click", showResetDialog)
  document.getElementById("cancelBtn")?.addEventListener("click", closeDropdown)

  const inputs = ["pick2Input", "pick3Input", "pick4Input", "pick5Input"]
  inputs.forEach((inputId, index) => {
    const el = document.getElementById(inputId)
    if (!el) return

    el.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault()
        if (index < inputs.length - 1) {
          document.getElementById(inputs[index + 1])?.focus()
        } else {
          handleAddNumbers()
        }
      }
    })
  })

  document.getElementById("confirmResetBtn")?.addEventListener("click", confirmReset)
  document.getElementById("cancelResetBtn")?.addEventListener("click", hideResetDialog)
  document.getElementById("addYesterdayBtn")?.addEventListener("click", handleAddYesterdayNumbers)
  document.getElementById("cancelYesterdayBtn")?.addEventListener("click", hideYesterdayPopup)

  document.addEventListener("click", (e) => {
    if (!e.target.closest(".dropdown-container")) closeDropdown()
  })
}

function updateDateTime() {
  const nyTime = getNyNow()
  const dateTimeString = `📅 ${nyTime.toLocaleDateString("en-US", {
    month: "numeric",
    day: "numeric",
    year: "numeric",
  })} ⏰ ${nyTime.toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
    hour12: true,
  })}`

  const currentTimeEl = document.getElementById("currentTime")
  if (currentTimeEl) currentTimeEl.textContent = dateTimeString
}

function updateCountdown() {
  const nyTime = getNyNow()
  const currentHour = nyTime.getHours()
  const currentMinute = nyTime.getMinutes()

  let targetSeconds = 0

  if (currentHour >= 10 && currentHour < 22) {
    const nextDraw = new Date(nyTime)

    if (currentMinute < 30) {
      nextDraw.setMinutes(30, 0, 0)
    } else {
      nextDraw.setHours(currentHour + 1, 0, 0, 0)
    }

    if (nextDraw.getHours() > 22 || (nextDraw.getHours() === 22 && nextDraw.getMinutes() > 0)) {
      nextDraw.setHours(22, 0, 0, 0)
    }

    targetSeconds = Math.max(0, Math.floor((nextDraw - nyTime) / 1000))
  } else if (currentHour === 22 && currentMinute === 0) {
    targetSeconds = 0
  } else {
    const nextTenAM = new Date(nyTime)
    if (currentHour >= 22) nextTenAM.setDate(nextTenAM.getDate() + 1)
    nextTenAM.setHours(10, 0, 0, 0)
    targetSeconds = Math.max(0, Math.floor((nextTenAM - nyTime) / 1000))
  }

  const hoursEl = document.getElementById("hours")
  const minutesEl = document.getElementById("minutes")
  const secondsEl = document.getElementById("seconds")

  if (hoursEl) hoursEl.textContent = String(Math.floor(targetSeconds / 3600)).padStart(2, "0")
  if (minutesEl) minutesEl.textContent = String(Math.floor((targetSeconds % 3600) / 60)).padStart(2, "0")
  if (secondsEl) secondsEl.textContent = String(targetSeconds % 60).padStart(2, "0")
}

function toggleDropdown() {
  isDropdownOpen = !isDropdownOpen
  const dropdown = document.getElementById("dropdownMenu")
  if (dropdown) dropdown.style.display = isDropdownOpen ? "block" : "none"
}

function closeDropdown() {
  isDropdownOpen = false
  const dropdown = document.getElementById("dropdownMenu")
  if (dropdown) dropdown.style.display = "none"
}

function clearInputs() {
  ;["pick2Input", "pick3Input", "pick4Input", "pick5Input", "selectedTime"].forEach((id) => {
    const el = document.getElementById(id)
    if (el) el.value = ""
  })
}

function handleAddNumbers() {
  const pick2 = digitsOnly(document.getElementById("pick2Input")?.value.trim())
  const pick3 = digitsOnly(document.getElementById("pick3Input")?.value.trim())
  const pick4 = digitsOnly(document.getElementById("pick4Input")?.value.trim())
  const pick5 = digitsOnly(document.getElementById("pick5Input")?.value.trim())
  const selectedTime = document.getElementById("selectedTime")?.value || ""

  if (pick2 && pick2.length !== 2) return alert("Pick 2 must have exactly 2 digits")
  if (pick3 && pick3.length !== 3) return alert("Pick 3 must have exactly 3 digits")
  if (pick4 && pick4.length !== 4) return alert("Pick 4 must have exactly 4 digits")
  if (pick5 && pick5.length !== 5) return alert("Pick 5 must have exactly 5 digits")
  if (!pick2 && !pick3 && !pick4 && !pick5) return alert("Please enter at least one result")
  if (!selectedTime) return alert("Please select a draw time")

  const dateLabel = makeDateLabelForDay(0)
  const isYesterday = false
  const slotKey = makeSlotKey(dateLabel, selectedTime, isYesterday)

  const box = sanitizeBox({
    id: `manual-${Date.now()}`,
    draw_id: `manual-${Date.now()}`,
    date: dateLabel,
    time: selectedTime,
    pick2: toDigits(pick2),
    pick3: toDigits(pick3),
    pick4: toDigits(pick4),
    pick5: toDigits(pick5),
    isYesterday,
    sortTs: makeSortTsForDay(selectedTime, 0),
    slotKey,
    source: "manual",
  })

  if (!box) return

  const idx = manualBoxes.findIndex((b) => b.slotKey === slotKey)
  if (idx >= 0) {
    manualBoxes[idx] = box
  } else {
    manualBoxes.unshift(box)
  }

  saveManualBoxes()
  renderNumberBoxes()
  clearInputs()
  closeDropdown()
}

function showYesterdayPopup() {
  const content = document.getElementById("yesterdayContent")
  if (!content) return

  content.innerHTML = ""

  yesterdayDrawTimes.forEach((time) => {
    const section = document.createElement("div")
    section.className = "yesterday-draw-section"
    section.setAttribute("data-time", time)

    section.innerHTML = `
      <h3 class="yesterday-time">${time}</h3>
      <div class="yesterday-inputs">
        <div class="yesterday-input-group">
          <label>P2</label>
          <input type="text" placeholder="12" class="yesterday-input">
        </div>
        <div class="yesterday-input-group">
          <label>P3</label>
          <input type="text" placeholder="123" class="yesterday-input">
        </div>
        <div class="yesterday-input-group">
          <label>P4</label>
          <input type="text" placeholder="1234" class="yesterday-input">
        </div>
        <div class="yesterday-input-group">
          <label>P5</label>
          <input type="text" placeholder="12345" class="yesterday-input">
        </div>
      </div>
    `

    content.appendChild(section)
  })

  const overlay = document.getElementById("yesterdayOverlay")
  if (overlay) overlay.style.display = "flex"
  closeDropdown()
}

function hideYesterdayPopup() {
  const overlay = document.getElementById("yesterdayOverlay")
  if (overlay) overlay.style.display = "none"
}

function handleAddYesterdayNumbers() {
  const added = []

  yesterdayDrawTimes.forEach((time) => {
    const section = document.querySelector(`[data-time="${time}"]`)
    if (!section) return

    const inputs = section.querySelectorAll(".yesterday-input")
    const pick2 = digitsOnly(inputs[0]?.value)
    const pick3 = digitsOnly(inputs[1]?.value)
    const pick4 = digitsOnly(inputs[2]?.value)
    const pick5 = digitsOnly(inputs[3]?.value)

    if (pick2 || pick3 || pick4 || pick5) {
      const dateLabel = makeDateLabelForDay(-1)
      const isYesterday = true
      const slotKey = makeSlotKey(dateLabel, time, isYesterday)

      const box = sanitizeBox({
        id: `manual-y-${time}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        draw_id: `manual-y-${time}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        date: dateLabel,
        time,
        pick2: toDigits(pick2),
        pick3: toDigits(pick3),
        pick4: toDigits(pick4),
        pick5: toDigits(pick5),
        isYesterday,
        sortTs: makeSortTsForDay(time, -1),
        slotKey,
        source: "manual",
      })

      if (box) added.push(box)
    }
  })

  if (added.length) {
    added.forEach((box) => {
      const idx = manualBoxes.findIndex((b) => b.slotKey === box.slotKey)
      if (idx >= 0) {
        manualBoxes[idx] = box
      } else {
        manualBoxes.unshift(box)
      }
    })

    saveManualBoxes()
    renderNumberBoxes()
  }

  hideYesterdayPopup()
}

function showResetDialog() {
  const overlay = document.getElementById("resetOverlay")
  if (overlay) overlay.style.display = "flex"
  closeDropdown()
}

function hideResetDialog() {
  const overlay = document.getElementById("resetOverlay")
  if (overlay) overlay.style.display = "none"
}

function confirmReset() {
  manualBoxes = []
  botBoxes = []
  try {
    localStorage.removeItem(STORAGE_KEY)
  } catch {}
  renderNumberBoxes()
  hideResetDialog()
}

function renderPickRow(label, values) {
  return `
    <div class="pyramid-row">
      <div class="pick-label">${label}</div>
      ${values.map((num) => `<div class="lottery-ball">${num}</div>`).join("")}
    </div>
  `
}

function getQuinielaData(box) {
  if (!box || !Array.isArray(box.pick3) || !Array.isArray(box.pick4)) return null
  if (box.pick3.length !== 3 || box.pick4.length !== 4) return null

  const pick3 = box.pick3.join("")
  const pick4 = box.pick4.join("")

  return [
    { label: "Q1", value: pick3.slice(-2) },
    { label: "Q2", value: pick4.slice(0, 2) },
    { label: "Q3", value: pick4.slice(-2) },
  ]
}

function renderQuinielaPanel(box) {
  const quinielas = getQuinielaData(box)
  if (!quinielas) return ""

  const medals = ["🥇", "🥈", "🥉"]

  return `
    <div class="quiniela-panel">
      <div class="quiniela-title">QUINIELAS</div>
      ${quinielas
        .map(
          (item, index) => `
            <div class="quiniela-item">
              <div class="quiniela-badge medal-badge">${medals[index]}</div>
              <div class="quiniela-pair">
                ${item.value
                  .split("")
                  .map((digit) => `<div class="quiniela-ball">${digit}</div>`)
                  .join("")}
              </div>
            </div>
          `
        )
        .join("")}
    </div>
  `
}

function boxHtml(box, index) {
  let rows = ""
  if (box.pick2.length) rows += renderPickRow("P2", box.pick2)
  if (box.pick3.length) rows += renderPickRow("P3", box.pick3)
  if (box.pick4.length) rows += renderPickRow("P4", box.pick4)
  if (box.pick5.length) rows += renderPickRow("P5", box.pick5)

  return `
    ${box.isYesterday ? '<div class="yesterday-banner">YESTERDAY</div>' : ""}
    ${index === 0 ? '<div class="live-indicator">LIVE</div>' : ""}
    <div class="box-header">
      <div class="date-display">🍀 ${box.date}</div>
      <div class="time-display">• ${box.time}</div>
    </div>
    <div class="box-content">
      <div class="pyramid-container">
        ${rows}
      </div>
      ${renderQuinielaPanel(box)}
    </div>
  `
}

function upsertBoxElement(grid, box, index) {
  const boxId = box.id
  const html = boxHtml(box, index)
  const className = `number-box ${index === 0 ? "live-box" : ""} ${box.isYesterday ? "yesterday-box" : ""}`.trim()

  let el = grid.querySelector(`[data-box-id="${boxId}"]`)

  if (!el) {
    el = document.createElement("div")
    el.setAttribute("data-box-id", boxId)
    el.className = className
    el.innerHTML = html
    return el
  }

  if (el.className !== className) el.className = className
  if (el.innerHTML !== html) el.innerHTML = html

  return el
}

function getDisplayBoxes() {
  const slotMap = new Map()

  botBoxes.forEach((box) => {
    slotMap.set(box.slotKey, box)
  })

  manualBoxes.forEach((box) => {
    slotMap.set(box.slotKey, box)
  })

  return Array.from(slotMap.values())
    .sort((a, b) => (b.sortTs || 0) - (a.sortTs || 0))
    .slice(0, 9)
}

function renderNumberBoxes() {
  const grid = document.getElementById("numbersGrid")
  const emptyState = document.getElementById("emptyState")
  if (!grid) return

  const desired = getDisplayBoxes()

  if (desired.length === 0) {
    grid.innerHTML = ""
    if (emptyState) emptyState.style.display = "flex"
    return
  }

  if (emptyState) emptyState.style.display = "none"

  const desiredIds = new Set(desired.map((b) => b.id))

  Array.from(grid.children).forEach((child) => {
    const boxId = child.getAttribute("data-box-id")
    if (!desiredIds.has(boxId)) child.remove()
  })

  desired.forEach((box, index) => {
    const el = upsertBoxElement(grid, box, index)
    const currentAtIndex = grid.children[index]

    if (currentAtIndex !== el) {
      grid.insertBefore(el, currentAtIndex || null)
    }
  })
}

function nyDate(iso) {
  if (!iso) return null
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return null
  return new Date(d.toLocaleString("en-US", { timeZone: "America/New_York" }))
}

function slotFromIso(iso) {
  const dny = nyDate(iso)
  if (!dny) return null

  const m = dny.getMinutes()
  dny.setMinutes(m < 30 ? 0 : 30, 0, 0)
  return dny
}

function makeBoxFromLatest(data) {
  if (!data || !data.draw_id) return null

  const slot = slotFromIso(data.draw_id)
  if (!slot) return null

  const dateLabel = slot.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })
  const timeLabel = slot.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })

  return sanitizeBox({
    id: `bot-${data.draw_id}`,
    draw_id: data.draw_id,
    date: dateLabel,
    time: timeLabel,
    pick2: toDigits(data.pick2),
    pick3: toDigits(data.pick3),
    pick4: toDigits(data.pick4),
    pick5: toDigits(data.pick5),
    isYesterday: false,
    sortTs: slot.getTime(),
    slotKey: makeSlotKey(dateLabel, timeLabel, false),
    source: "bot",
  })
}

function isTodayNY(iso) {
  if (!iso) return false

  const d = new Date(iso)
  const ny = new Date(d.toLocaleString("en-US", { timeZone: "America/New_York" }))
  const now = getNyNow()

  return (
    ny.getFullYear() === now.getFullYear() &&
    ny.getMonth() === now.getMonth() &&
    ny.getDate() === now.getDate()
  )
}

function hasExactDigits(str, len) {
  return typeof str === "string" && new RegExp(`^\\d{${len}}$`).test(str)
}

function resultIsDisplayable(data) {
  if (!data || !data.draw_id) return false
  if (!isTodayNY(data.draw_id)) return false

  return (
    hasExactDigits(data.pick2, 2) ||
    hasExactDigits(data.pick3, 3) ||
    hasExactDigits(data.pick4, 4) ||
    hasExactDigits(data.pick5, 5)
  )
}

async function loadLatest() {
  try {
    const r = await fetch("./latest.json?t=" + Date.now(), { cache: "no-store" })
    if (!r.ok) return

    const raw = await r.json()
    const dataArr = Array.isArray(raw) ? raw : raw && typeof raw === "object" ? [raw] : []
    if (!dataArr.length) {
      botBoxes = []
      renderNumberBoxes()
      return
    }

    const visible = dataArr
      .filter(resultIsDisplayable)
      .sort((a, b) => (a.draw_id < b.draw_id ? 1 : -1))

    const slotMap = new Map()

    for (const item of visible) {
      const box = makeBoxFromLatest(item)
      if (!box) continue
      slotMap.set(box.slotKey, box)
    }

    botBoxes = Array.from(slotMap.values())
    renderNumberBoxes()
  } catch (err) {
    console.error("loadLatest error:", err)
  }
}
