/* Waga_RP - logika dashboardu */
(() => {
  "use strict";

  const state = { user: "all", days: 30, sport: "all", series: "weight_kg",
                  measurements: [], activities: [] };
  const charts = {};
  const $ = (sel) => document.querySelector(sel);

  // ---------------------------------------------------------------- pomocnicze
  const fmt = (v, digits = 1, unit = "") =>
    (v === null || v === undefined || Number.isNaN(v))
      ? "—" : `${Number(v).toFixed(digits)}${unit ? `<small>${unit}</small>` : ""}`;

  const num = (v, digits = 1) =>
    (v === null || v === undefined) ? "—" : Number(v).toFixed(digits);

  const dateLabel = (iso) => {
    const d = new Date(iso);
    return d.toLocaleDateString("pl-PL", { day: "2-digit", month: "short" });
  };

  const dateTimeLabel = (iso) => {
    const d = new Date(iso);
    return d.toLocaleString("pl-PL",
      { day: "2-digit", month: "2-digit", year: "2-digit", hour: "2-digit", minute: "2-digit" });
  };

  const duration = (seconds) => {
    if (!seconds) return "—";
    const h = Math.floor(seconds / 3600), m = Math.round((seconds % 3600) / 60);
    return h ? `${h} h ${m} min` : `${m} min`;
  };

  const pace = (metres, seconds) => {              // min/km
    if (!metres || !seconds) return "—";
    const secPerKm = seconds / (metres / 1000);
    return `${Math.floor(secPerKm / 60)}:${String(Math.round(secPerKm % 60)).padStart(2, "0")} /km`;
  };

  const css = (name) => getComputedStyle(document.body).getPropertyValue(name).trim();

  const monday = (date) => {
    const d = new Date(date);
    d.setHours(0, 0, 0, 0);
    d.setDate(d.getDate() - ((d.getDay() + 6) % 7));
    return d;
  };

  // ---------------------------------------------------------------- wykresy
  function baseOptions() {
    const grid = css("--border"), muted = css("--muted");
    return {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: css("--surface-2"), titleColor: css("--text"),
          bodyColor: css("--text"), borderColor: grid, borderWidth: 1,
          padding: 10, cornerRadius: 8, displayColors: false,
        },
      },
      scales: {
        x: { grid: { display: false }, ticks: { color: muted, maxRotation: 0, autoSkipPadding: 18 } },
        y: { grid: { color: grid, drawTicks: false }, border: { display: false },
             ticks: { color: muted, padding: 8 } },
      },
    };
  }

  function drawChart(key, canvasId, config) {
    charts[key]?.destroy();
    charts[key] = new Chart($(canvasId), config);
  }

  const SERIES_META = {
    weight_kg: { label: "Waga", unit: "kg" },
    fat_percentage: { label: "Tkanka tłuszczowa", unit: "%" },
    muscle_mass: { label: "Masa mięśniowa", unit: "kg" },
    water_percentage: { label: "Woda", unit: "%" },
    bmi: { label: "BMI", unit: "" },
  };

  function renderWeightChart() {
    const meta = SERIES_META[state.series];
    const points = state.measurements.filter((m) => m[state.series] !== null);
    $("#weightEmpty").hidden = points.length > 0;

    const accent = css("--accent");
    drawChart("weight", "#weightChart", {
      type: "line",
      data: {
        labels: points.map((m) => dateLabel(m.measured_at)),
        datasets: [{
          data: points.map((m) => m[state.series]),
          borderColor: accent,
          backgroundColor: (ctx) => {
            const { ctx: c, chartArea } = ctx.chart;
            if (!chartArea) return "transparent";
            const g = c.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
            g.addColorStop(0, accent + "55");
            g.addColorStop(1, accent + "00");
            return g;
          },
          borderWidth: 2.5, fill: true, tension: .35,
          pointRadius: points.length > 60 ? 0 : 3,
          pointBackgroundColor: accent, pointHoverRadius: 6,
        }],
      },
      options: {
        ...baseOptions(),
        plugins: {
          ...baseOptions().plugins,
          tooltip: {
            ...baseOptions().plugins.tooltip,
            callbacks: {
              title: (items) => dateTimeLabel(points[items[0].dataIndex].measured_at),
              label: (item) => `${meta.label}: ${num(item.parsed.y, 2)} ${meta.unit}`,
            },
          },
        },
        scales: {
          ...baseOptions().scales,
          y: { ...baseOptions().scales.y, ticks: { color: css("--muted"), padding: 8,
               callback: (v) => `${v} ${meta.unit}` } },
        },
      },
    });
  }

  function renderComposition(latest) {
    if (!latest || !latest.fat_percentage) {
      charts.composition?.destroy();
      return;
    }
    const fat = latest.weight_kg * latest.fat_percentage / 100;
    const muscle = latest.muscle_mass || 0;
    const bone = latest.bone_mass || 0;
    const rest = Math.max(0, latest.weight_kg - fat - muscle - bone);

    drawChart("composition", "#compositionChart", {
      type: "doughnut",
      data: {
        labels: ["Tłuszcz", "Mięśnie", "Kości", "Pozostałe"],
        datasets: [{
          data: [fat, muscle, bone, rest].map((v) => +v.toFixed(2)),
          backgroundColor: [css("--accent-2"), css("--accent"), css("--muted"), css("--surface-2")],
          borderColor: css("--surface"), borderWidth: 3, hoverOffset: 6,
        }],
      },
      options: {
        responsive: true, maintainAspectRatio: false, cutout: "62%",
        plugins: {
          legend: { position: "bottom", labels: { color: css("--muted"), boxWidth: 10,
                    usePointStyle: true, pointStyle: "circle", padding: 14 } },
          tooltip: { ...baseOptions().plugins.tooltip, displayColors: true,
                     callbacks: { label: (i) => ` ${i.label}: ${num(i.parsed, 2)} kg` } },
        },
      },
    });
  }

  function renderVolumeChart() {
    const weeks = new Map();
    state.activities.forEach((a) => {
      const key = monday(a.start_date_local || a.start_date).toISOString().slice(0, 10);
      weeks.set(key, (weeks.get(key) || 0) + (a.distance_m || 0) / 1000);
    });
    const keys = [...weeks.keys()].sort();
    drawChart("volume", "#volumeChart", {
      type: "bar",
      data: {
        labels: keys.map(dateLabel),
        datasets: [{
          data: keys.map((k) => +weeks.get(k).toFixed(1)),
          backgroundColor: css("--accent-2"), borderRadius: 6, maxBarThickness: 34,
        }],
      },
      options: {
        ...baseOptions(),
        plugins: {
          ...baseOptions().plugins,
          tooltip: { ...baseOptions().plugins.tooltip,
            callbacks: { title: (i) => `tydzień od ${i[0].label}`,
                         label: (i) => `${i.parsed.y} km` } },
        },
        scales: { ...baseOptions().scales,
          y: { ...baseOptions().scales.y, ticks: { color: css("--muted"), padding: 8,
               callback: (v) => `${v} km` } } },
      },
    });
  }

  function renderPaceChart() {
    const runs = [...state.activities]
      .filter((a) => a.distance_m > 500 && a.moving_time_s)
      .sort((a, b) => (a.start_date_local || "").localeCompare(b.start_date_local || ""));
    const values = runs.map((a) => +(a.moving_time_s / 60 / (a.distance_m / 1000)).toFixed(2));

    drawChart("pace", "#paceChart", {
      type: "line",
      data: {
        labels: runs.map((a) => dateLabel(a.start_date_local || a.start_date)),
        datasets: [{
          data: values, borderColor: css("--good"), backgroundColor: css("--good"),
          borderWidth: 2, tension: .3, pointRadius: runs.length > 40 ? 0 : 3, fill: false,
        }],
      },
      options: {
        ...baseOptions(),
        plugins: {
          ...baseOptions().plugins,
          tooltip: { ...baseOptions().plugins.tooltip,
            callbacks: {
              title: (i) => runs[i[0].dataIndex].name || "",
              label: (i) => `tempo ${Math.floor(i.parsed.y)}:${String(Math.round((i.parsed.y % 1) * 60)).padStart(2, "0")} /km`,
            } },
        },
        scales: { ...baseOptions().scales,
          y: { ...baseOptions().scales.y, reverse: true,
               ticks: { color: css("--muted"), padding: 8,
                        callback: (v) => `${Math.floor(v)}:${String(Math.round((v % 1) * 60)).padStart(2, "0")}` } } },
      },
    });
  }

  // ---------------------------------------------------------------- tabele / KPI
  const IDENT_LABEL = {
    interval: ["przedział", "trafił w przedział predykcyjny profilu"],
    fallback_last: ["najbliższy", "poza przedziałami — przypisany do najbliższej ostatniej wagi"],
    fallback_ref: ["startowa", "poza przedziałami — przypisany po wadze startowej profilu"],
    unassigned: ["brak", "nie pasował do żadnego profilu"],
  };

  function identBadge(m) {
    const [label, title] = IDENT_LABEL[m.identify_method] || ["—", ""];
    const score = (m.identify_score === null || m.identify_score === undefined)
      ? "" : ` ${m.identify_score > 0 ? "+" : ""}${Number(m.identify_score).toFixed(1)} kg`;
    const who = m.user_display || "nieprzypisany";
    return `<span title="${title}${score ? ` (${score.trim()})` : ""}">${who}
            <em class="badge ${m.identify_method || "unassigned"}">${label}${score}</em></span>`;
  }

  function renderMeasurementTable() {
    $("#measurementTable tbody").innerHTML = [...state.measurements].reverse().map((m) => `
      <tr>
        <td>${dateTimeLabel(m.measured_at)}</td>
        <td>${identBadge(m)}</td>
        <td><strong>${num(m.weight_kg, 2)} kg</strong></td>
        <td>${num(m.fat_percentage)} %</td>
        <td>${num(m.muscle_mass)} kg</td>
        <td>${num(m.water_percentage)} %</td>
        <td>${num(m.bone_mass, 2)} kg</td>
        <td>${num(m.visceral_fat, 0)}</td>
        <td>${num(m.bmi)}</td>
        <td>${m.impedance ?? "—"} Ω</td>
      </tr>`).join("") || `<tr><td colspan="10">Brak pomiarów.</td></tr>`;
  }

  function renderActivityTable() {
    $("#activityTable tbody").innerHTML = state.activities.slice(0, 60).map((a) => `
      <tr>
        <td>${dateTimeLabel(a.start_date_local || a.start_date)}</td>
        <td>${(a.name || "").slice(0, 34)}</td>
        <td>${((a.distance_m || 0) / 1000).toFixed(2)} km</td>
        <td>${duration(a.moving_time_s)}</td>
        <td>${pace(a.distance_m, a.moving_time_s)}</td>
        <td>${a.average_heartrate ? Math.round(a.average_heartrate) + " bpm" : "—"}</td>
      </tr>`).join("") || `<tr><td colspan="6">Brak aktywności — uruchom sync Stravy.</td></tr>`;
  }

  function renderSummary(s) {
    const m = s.latest;
    $("#kpiWeight").innerHTML = m ? fmt(m.weight_kg, 2, " kg") : "—";
    const d = s.weight_delta;
    $("#kpiWeightDelta").innerHTML = (d === null || d === undefined)
      ? (m ? `pomiar: ${dateTimeLabel(m.measured_at)}` : "brak danych")
      : `<span class="${d > 0 ? "up" : "down"}">${d > 0 ? "▲" : "▼"} ${Math.abs(d).toFixed(2)} kg</span> w ${s.period_days || "całym"} ${s.period_days ? "dniach" : "okresie"}`;

    $("#kpiFat").innerHTML = m ? fmt(m.fat_percentage, 1, " %") : "—";
    $("#kpiFatFoot").textContent = m?.fat_percentage
      ? `≈ ${(m.weight_kg * m.fat_percentage / 100).toFixed(1)} kg tłuszczu` : "";
    $("#kpiMuscle").innerHTML = m ? fmt(m.muscle_mass, 1, " kg") : "—";
    $("#kpiMuscleFoot").textContent = m?.bone_mass ? `kości ${num(m.bone_mass, 2)} kg` : "";
    $("#kpiBmi").innerHTML = m ? fmt(m.bmi, 1) : "—";
    $("#kpiBmiFoot").textContent = m?.ideal_weight ? `waga docelowa ${num(m.ideal_weight, 1)} kg` : "";
    $("#kpiWater").innerHTML = m ? fmt(m.water_percentage, 1, " %") : "—";
    $("#kpiBmr").innerHTML = m ? fmt(m.bmr, 0, " kcal") : "—";
    $("#kpiMetaAge").textContent = m?.metabolic_age ? `wiek metaboliczny ${num(m.metabolic_age, 0)} lat` : "";

    const a = s.activity;
    $("#kpiDistance").innerHTML = fmt((a.distance_m || 0) / 1000, 1, " km");
    $("#kpiRunCount").textContent = `${a.count} aktywności`;
    $("#kpiTime").innerHTML = duration(a.moving_time_s);
    $("#kpiAvgPace").textContent = a.distance_m
      ? `średnie tempo ${pace(a.distance_m, a.moving_time_s)}` : "";
    $("#kpiElev").innerHTML = fmt(a.elevation_m || 0, 0, " m");
    $("#kpiHr").innerHTML = a.avg_heartrate ? fmt(a.avg_heartrate, 0, " bpm") : "—";
    $("#kpiLastRun").textContent = s.last_activity
      ? `ostatnia: ${dateLabel(s.last_activity.start_date_local)}` : "";

    const details = [
      ["Data pomiaru", m ? dateTimeLabel(m.measured_at) : "—"],
      ["Waga", m ? `${num(m.weight_kg, 2)} kg` : "—"],
      ["Impedancja", m?.impedance ? `${m.impedance} Ω` : "—"],
      ["Tkanka tłuszczowa", m ? `${num(m.fat_percentage)} %` : "—"],
      ["Masa mięśniowa", m ? `${num(m.muscle_mass)} kg` : "—"],
      ["Masa kostna", m ? `${num(m.bone_mass, 2)} kg` : "—"],
      ["Woda", m ? `${num(m.water_percentage)} %` : "—"],
      ["Białko", m ? `${num(m.protein_percentage)} %` : "—"],
      ["Tłuszcz trzewny", m ? num(m.visceral_fat, 0) : "—"],
      ["LBM", m ? `${num(m.lbm)} kg` : "—"],
      ["BMR", m ? `${num(m.bmr, 0)} kcal` : "—"],
      ["Wiek metaboliczny", m ? `${num(m.metabolic_age, 0)} lat` : "—"],
      ["Zakres w okresie", s.weight.count
        ? `${num(s.weight.min, 1)} – ${num(s.weight.max, 1)} kg (śr. ${num(s.weight.avg, 1)})` : "—"],
    ];
    $("#latestDetails").innerHTML = details
      .map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("");

    renderComposition(m);
  }

  // ---------------------------------------------------------------- dane
  async function getJSON(url) {
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`${url} -> ${resp.status}`);
    return resp.json();
  }

  async function refresh() {
    const q = `user=${encodeURIComponent(state.user)}&days=${state.days}`;
    const [summary, meas, acts] = await Promise.all([
      getJSON(`/api/summary?${q}`),
      getJSON(`/api/measurements?${q}`),
      getJSON(`/api/activities?days=${state.days}&sport=${encodeURIComponent(state.sport)}`),
    ]);
    state.measurements = meas.measurements;
    state.activities = acts.activities;

    const sportSelect = $("#sportSelect");
    if (sportSelect.options.length <= 1 && acts.sports.length) {
      acts.sports.forEach((s) => sportSelect.add(new Option(s, s)));
      sportSelect.value = state.sport;
    }

    renderSummary(summary);
    renderWeightChart();
    renderVolumeChart();
    renderPaceChart();
    renderMeasurementTable();
    renderActivityTable();
  }

  function redrawAll() {                 // po zmianie motywu
    renderWeightChart(); renderVolumeChart(); renderPaceChart();
    const last = state.measurements[state.measurements.length - 1];
    renderComposition(last);
  }

  async function boot() {
    const [health, users, predictions] = await Promise.all([
      getJSON("/api/health"), getJSON("/api/users"), getJSON("/api/predictions"),
    ]);

    $("#healthLine").textContent =
      `${health.measurements} pomiarów · ${health.activities} treningów · ` +
      `Garmin: ${health.garmin_connected
        ? `${health.garmin_days} dni${health.garmin_last_day ? ` (do ${health.garmin_last_day})` : ""}`
        : "niepołączony"}`;

    const sel = $("#userSelect");
    sel.add(new Option("wszyscy", "all"));
    users.users.forEach((u) => sel.add(new Option(`${u.display_name} (${u.measurements})`, u.username)));
    if (users.users.length === 1) { sel.value = users.users[0].username; state.user = sel.value; }

    $("#forecastMessage").textContent = predictions.message;
    $("#forecastPlanned").innerHTML = (predictions.planned || [])
      .map((p) => `<li>${p}</li>`).join("");

    await refresh();
    if (location.hash) showTab(location.hash.slice(1));
  }

  // ---------------------------------------------------------------- zdarzenia
  $("#userSelect").addEventListener("change", (e) => { state.user = e.target.value; refresh(); });
  $("#rangeSelect").addEventListener("change", (e) => { state.days = +e.target.value; refresh(); });
  $("#sportSelect").addEventListener("change", (e) => { state.sport = e.target.value; refresh(); });

  function showTab(name) {
    const tab = document.querySelector(`.tab[data-tab="${name}"]`);
    if (!tab) return;
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    tab.classList.add("active");
    $(`#tab-${name}`).classList.add("active");
    Object.values(charts).forEach((c) => c.resize());
  }

  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      location.hash = tab.dataset.tab;          // zakladka w adresie - da sie linkowac
      showTab(tab.dataset.tab);
    });
  });
  addEventListener("hashchange", () => showTab(location.hash.slice(1)));

  $("#weightSeriesChips").addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (!chip) return;
    document.querySelectorAll("#weightSeriesChips .chip").forEach((c) => c.classList.remove("active"));
    chip.classList.add("active");
    state.series = chip.dataset.series;
    renderWeightChart();
  });

  $("#themeToggle").addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("waga-theme", next);
    redrawAll();
  });

  document.documentElement.dataset.theme =
    localStorage.getItem("waga-theme")
    || (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");

  boot().catch((err) => {
    $("#healthLine").textContent = `Błąd ładowania danych: ${err.message}`;
  });

  setInterval(() => refresh().catch(() => {}), 60_000);   // odswiezanie co minute
})();
