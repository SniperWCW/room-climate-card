class RoomClimateCard extends HTMLElement {
  constructor() {
    super();
    this._renderScheduled = false;
    this._outsideForecast = [];
    this._outsideForecastEntityId = null;
    this._outsideForecastUnsubscribe = null;
    this._outsideForecastRequestId = 0;
    this._outsideForecastLastRequestAt = 0;
  }

  static getConfigElement() {
    return document.createElement("room-climate-card-editor");
  }

  static getStubConfig(hass) {
    const rooms = RoomClimateCardEditor.detectRooms(hass);
    return {
      mode: "detailed",
      columns: 2,
      outside_absolute_humidity: RoomClimateCardEditor.findGlobalOutsideHumidity(hass),
      outside_weather: RoomClimateCardEditor.findGlobalWeatherEntity(hass),
      rooms: rooms.length ? rooms : [RoomClimateCardEditor.createEmptyRoom()],
    };
  }

  setConfig(config) {
    this.config = {
      mode: "detailed",
      columns: 2,
      outside_absolute_humidity: "",
      outside_weather: "",
      rooms: [],
      ...config,
    };

    this.ensureOutsideForecast();
  }

  set hass(hass) {
    this._hass = hass;
    this.ensureOutsideForecast();
    this.scheduleRender();
  }

  disconnectedCallback() {
    this.clearOutsideForecastSubscription();
  }

  scheduleRender() {
    if (this._renderScheduled) return;
    this._renderScheduled = true;
    requestAnimationFrame(() => {
      this._renderScheduled = false;
      this.render();
    });
  }

  getState(entity) {
    return entity && this._hass?.states?.[entity] ? this._hass.states[entity].state : null;
  }

  getAttributes(entity) {
    return entity && this._hass?.states?.[entity] ? this._hass.states[entity].attributes || {} : {};
  }

  getOutsideWeatherMetrics() {
    const entityId = this.config?.outside_weather;
    if (!entityId) {
      return {
        temperature: null,
        humidity: null,
        windSpeed: null,
      };
    }

    const stateObj = this._hass?.states?.[entityId];
    const attrs = stateObj?.attributes || {};
    const temperature = Number(attrs.temperature);
    const humidity = Number(attrs.humidity);
    const windSpeed = Number(attrs.wind_speed);

    return {
      temperature: Number.isFinite(temperature) ? temperature : null,
      humidity: Number.isFinite(humidity) ? humidity : null,
      windSpeed: Number.isFinite(windSpeed) ? windSpeed : null,
    };
  }

  clearOutsideForecastSubscription() {
    if (typeof this._outsideForecastUnsubscribe === "function") {
      this._outsideForecastUnsubscribe();
    }

    this._outsideForecastUnsubscribe = null;
    this._outsideForecastRequestId += 1;
    this._outsideForecastLastRequestAt = 0;
  }

  normalizeForecastEntries(forecast) {
    if (!Array.isArray(forecast)) return [];

    return forecast
      .map((entry) => ({
        datetime: entry?.datetime || entry?.datetime_iso || entry?.time || null,
        temperature: Number(entry?.temperature),
        humidity: Number(entry?.humidity),
        windSpeed: Number(entry?.wind_speed),
      }))
      .filter((entry) => entry.datetime && Number.isFinite(entry.temperature));
  }

  extractForecastEntries(payload, entityId) {
    if (Array.isArray(payload)) {
      return this.normalizeForecastEntries(payload);
    }

    if (!payload || typeof payload !== "object") {
      return [];
    }

    if (Array.isArray(payload.forecast)) {
      return this.normalizeForecastEntries(payload.forecast);
    }

    if (entityId && Array.isArray(payload?.[entityId]?.forecast)) {
      return this.normalizeForecastEntries(payload[entityId].forecast);
    }

    return [];
  }

  setOutsideForecast(entityId, payload) {
    const forecast = this.extractForecastEntries(payload, entityId);
    this._outsideForecastEntityId = entityId;
    this._outsideForecast = forecast;
    this.scheduleRender();
  }

  async requestOutsideForecast(entityId, requestId) {
    const connection = this._hass?.connection;
    if (!connection?.subscribeMessage) {
      return false;
    }

    try {
      const unsubscribe = await connection.subscribeMessage(
        (message) => {
          if (requestId !== this._outsideForecastRequestId || this.config?.outside_weather !== entityId) return;
          this.setOutsideForecast(entityId, message);
        },
        {
          type: "weather/subscribe_forecast",
          forecast_type: "hourly",
          entity_id: entityId,
        }
      );

      if (requestId !== this._outsideForecastRequestId || this.config?.outside_weather !== entityId) {
        if (typeof unsubscribe === "function") {
          unsubscribe();
        }
        return false;
      }

      this._outsideForecastUnsubscribe = unsubscribe;
      return true;
    } catch (_error) {
      return false;
    }
  }

  ensureOutsideForecast() {
    const entityId = this.config?.outside_weather;
    if (!entityId) {
      this.clearOutsideForecastSubscription();
      this._outsideForecast = [];
      this._outsideForecastEntityId = null;
      return;
    }

    if (this._outsideForecastEntityId === entityId && (this._outsideForecast.length || this._outsideForecastUnsubscribe)) {
      return;
    }

    this.clearOutsideForecastSubscription();
    this._outsideForecast = [];
    this._outsideForecastEntityId = entityId;

    const stateForecast = this.extractForecastEntries(this.getAttributes(entityId), entityId);
    if (stateForecast.length) {
      this._outsideForecast = stateForecast;
    }

    if (Date.now() - this._outsideForecastLastRequestAt < 15000) {
      return;
    }

    this._outsideForecastLastRequestAt = Date.now();
    const requestId = this._outsideForecastRequestId;
    this.requestOutsideForecast(entityId, requestId);
  }

  getOutsideForecast() {
    const entityId = this.config?.outside_weather;
    if (!entityId) return [];

    if (this._outsideForecastEntityId === entityId && this._outsideForecast.length) {
      return this._outsideForecast;
    }

    return this.extractForecastEntries(this.getAttributes(entityId), entityId);
  }

  formatForecastTime(datetimeValue) {
    const date = new Date(datetimeValue);
    if (Number.isNaN(date.getTime())) return null;

    return date.toLocaleTimeString("de-DE", {
      hour: "2-digit",
      minute: "2-digit",
      timeZone: "Europe/Berlin",
    });
  }

  getRecommendedCoolTempThreshold(room) {
    const profile = this.getRoomProfile(room);
    return Math.min(profile.tempMax + 4, 26);
  }

  getNextCoolingWindow(room) {
    const threshold = this.getRecommendedCoolTempThreshold(room);
    const forecast = this.getOutsideForecast();
    const next = forecast.find((entry) => entry.temperature <= threshold);

    if (!next) {
      return { threshold, timeText: null };
    }

    return {
      threshold,
      timeText: this.formatForecastTime(next.datetime),
    };
  }

  getNextVentilationWindowLine(room, coolingAdvice) {
    if (!room.window || !coolingAdvice || coolingAdvice.level === "cooling" || coolingAdvice.level === "open") {
      return null;
    }

    const coolingWindow = this.getNextCoolingWindow(room);
    if (!coolingWindow.timeText) {
      return `🕒 Nächstes Lüftungsfenster: sobald die Außentemperatur unter ${coolingWindow.threshold
        .toFixed(1)
        .replace(".", ",")} °C fällt.`;
    }

    return `🕒 Nächstes Lüftungsfenster: ab ${coolingWindow.timeText} Uhr, wenn die Außentemperatur unter ${coolingWindow.threshold
      .toFixed(1)
      .replace(".", ",")} °C fällt.`;
  }

  getNumber(entity) {
    const value = parseFloat(this.getState(entity));
    return Number.isFinite(value) ? value : null;
  }

  getRoomProfile(room) {
    const profiles = {
      living: {
        label: "Wohnraum",
        humidityMin: 40,
        humidityMax: 55,
        tempMin: 16,
        tempMax: 22,
        ventModerateDiff: 1.0,
        ventStrongDiff: 1.5,
      },
      bedroom: {
        label: "Schlafzimmer",
        humidityMin: 40,
        humidityMax: 55,
        tempMin: 16,
        tempMax: 20,
        ventModerateDiff: 0.8,
        ventStrongDiff: 1.2,
      },
      child: {
        label: "Kinderzimmer",
        humidityMin: 40,
        humidityMax: 55,
        tempMin: 16,
        tempMax: 22,
        ventModerateDiff: 1.0,
        ventStrongDiff: 1.4,
      },
      bathroom: {
        label: "Badezimmer",
        humidityMin: 50,
        humidityMax: 65,
        tempMin: 20,
        tempMax: 23,
        ventModerateDiff: 0.6,
        ventStrongDiff: 1.0,
      },
      kitchen: {
        label: "Küche",
        humidityMin: 45,
        humidityMax: 60,
        tempMin: 18,
        tempMax: 20,
        ventModerateDiff: 0.8,
        ventStrongDiff: 1.3,
      },
      basement: {
        label: "Keller",
        humidityMin: 50,
        humidityMax: 65,
        tempMin: 10,
        tempMax: 15,
        ventModerateDiff: 1.5,
        ventStrongDiff: 2.0,
      },
      office: {
        label: "Büro",
        humidityMin: 40,
        humidityMax: 55,
        tempMin: 18,
        tempMax: 22,
        ventModerateDiff: 1.0,
        ventStrongDiff: 1.5,
      },
      default: {
        label: "Standard",
        humidityMin: 40,
        humidityMax: 55,
        tempMin: 18,
        tempMax: 22,
        ventModerateDiff: 1.0,
        ventStrongDiff: 1.5,
      },
    };

    return profiles[room.room_type] || profiles.default;
  }

  getVentilationContext(room) {
    const profile = this.getRoomProfile(room);
    const insideTemp = this.getNumber(room.temperature);
    const insideRel = this.getNumber(room.humidity);
    const insideAbs = this.getNumber(room.inside_absolute_humidity);
    const outsideAbs = this.getNumber(room.outside_absolute_humidity || this.config.outside_absolute_humidity);
    const outsideWeather = this.getOutsideWeatherMetrics();
    const outsideTemp = outsideWeather.temperature;
    const outsideWind = outsideWeather.windSpeed;
    const hasWindowSensor = Boolean(room.window);
    const windowState = this.getState(room.window);
    const windowOpen = ["on", "open", "tilted"].includes(windowState);
    const humidityHigh = insideRel !== null && insideRel >= profile.humidityMax;
    const humidityVeryHigh = insideRel !== null && insideRel >= profile.humidityMax + 5;
    const humidityTooDry = insideRel !== null && insideRel < Math.max(profile.humidityMin - 5, 35);
    const moderateDiff = profile.ventModerateDiff ?? 1.0;
    const strongDiff = profile.ventStrongDiff ?? 1.5;
    const diff = insideAbs !== null && outsideAbs !== null ? insideAbs - outsideAbs : null;
    const coolingDelta = insideTemp !== null && outsideTemp !== null ? insideTemp - outsideTemp : null;
    const canCool = coolingDelta !== null && insideTemp >= 27 && coolingDelta >= 2;
    const strongCooling = coolingDelta !== null && insideTemp >= 29 && coolingDelta >= 4;

    return {
      profile,
      insideTemp,
      insideRel,
      insideAbs,
      outsideAbs,
      outsideTemp,
      outsideWind,
      hasWindowSensor,
      windowState,
      windowOpen,
      humidityHigh,
      humidityVeryHigh,
      humidityTooDry,
      moderateDiff,
      strongDiff,
      diff,
      coolingDelta,
      canCool,
      strongCooling,
    };
  }

  calculateDehumidifyAdvice(room) {
    const ctx = this.getVentilationContext(room);

    if (!ctx.hasWindowSensor) {
      return null;
    }

    if (ctx.windowOpen) {
      return { icon: "🪟", level: "open", text: "Fenster ist bereits offen" };
    }

    if (ctx.insideRel === null) {
      return { icon: "❓", level: "unknown", text: "Luftfeuchtigkeit nicht verfügbar" };
    }

    if (ctx.humidityTooDry) {
      return {
        icon: "🏜️",
        level: "dry",
        text: `Luft zu trocken (${ctx.insideRel.toFixed(0)} %)`,
      };
    }

    if (ctx.insideAbs === null || ctx.outsideAbs === null) {
      if (ctx.humidityVeryHigh) {
        return {
          icon: "💧",
          level: "observe",
          text: "Raum ist feucht, aber ohne Innen-/Außenvergleich keine sichere Entfeuchtungsaussage",
        };
      }

      return { icon: "🌬️", level: "neutral", text: "Fenster geschlossen halten. Für Entfeuchtung fehlt aktuell ein belastbarer Außenvergleich." };
    }

    const duration = this.getVentilationDuration(room, ctx.diff, Number.isFinite(ctx.outsideWind) ? ctx.outsideWind : null);

    if (ctx.humidityVeryHigh && ctx.diff >= ctx.strongDiff) {
      return {
        icon: "💨",
        level: "recommended",
        text: `Entfeuchten sinnvoll, ca. ${duration} Min.`,
      };
    }

    if (ctx.humidityHigh && ctx.diff >= ctx.moderateDiff) {
      return {
        icon: "💨",
        level: "short",
        text: `Leichtes Entfeuchten sinnvoll, ca. ${duration} Min.`,
      };
    }

    if (ctx.humidityHigh && ctx.diff <= 0.3) {
      return { icon: "⛔", level: "avoid", text: "Nicht sinnvoll - außen kaum trockener" };
    }

    if (ctx.humidityHigh) {
      return {
        icon: "ℹ️",
        level: "observe",
        text: "Feuchte erhöht, aber aktuell kein klarer Entfeuchtungsvorteil",
      };
    }

    return { icon: "🌬️", level: "neutral", text: "Fenster geschlossen halten. Eine Entfeuchtung durch Lüften ist aktuell nicht nötig." };
  }

  calculateCoolingAdvice(room) {
    const ctx = this.getVentilationContext(room);
    const coolingWindow = this.getNextCoolingWindow(room);

    if (!ctx.hasWindowSensor) {
      return null;
    }

    if (ctx.windowOpen) {
      return { icon: "🪟", level: "open", text: "Fenster ist bereits offen" };
    }

    if (ctx.insideTemp === null || ctx.outsideTemp === null || ctx.coolingDelta === null) {
      return { icon: "🌡️", level: "neutral", text: "Fenster geschlossen halten. Für Abkühlung fehlt aktuell ein belastbarer Außenvergleich." };
    }

    if (!ctx.canCool) {
      return {
        icon: "🌡️",
        level: "neutral",
        text: "Fenster geschlossen halten. Außenluft bringt derzeit keine nennenswerte Abkühlung.",
      };
    }

    const duration = this.getCoolingDuration(ctx.coolingDelta, Number.isFinite(ctx.outsideWind) ? ctx.outsideWind : null);

    if (ctx.strongCooling && (ctx.diff === null || ctx.diff > -1.0)) {
      return { icon: "🧊", level: "cooling", text: `Fenster öffnen. Abkühlen ist jetzt sinnvoll, ca. ${duration} Min.` };
    }

    if (ctx.diff !== null && ctx.diff <= -1.5) {
      return { icon: "💧", level: "observe", text: `Fenster nur kurz öffnen. Abkühlung ist möglich, aber außen ist es deutlich feuchter (${duration} Min.).` };
    }

    return { icon: "🧊", level: "cooling", text: `Fenster kurz öffnen. Leichtes Abkühlen ist sinnvoll, ca. ${duration} Min.` };
  }

  calculateVentilation(room) {
    const dehumidify = this.calculateDehumidifyAdvice(room);
    const cooling = this.calculateCoolingAdvice(room);

    if (!dehumidify && !cooling) {
      return null;
    }

    if (dehumidify?.level === "recommended" || dehumidify?.level === "short") {
      return dehumidify;
    }

    if (cooling?.level === "cooling") {
      return cooling;
    }

    if (dehumidify?.level === "avoid" || dehumidify?.level === "dry") {
      return dehumidify;
    }

    return dehumidify || cooling;
  }

  getVentilationDuration(room, diff, windSpeed = null) {
    let duration;
    switch (room.room_type) {
      case "bathroom":
        if (diff >= 4) duration = 5;
        else if (diff >= 2.5) duration = 7;
        else if (diff >= 1.5) duration = 10;
        else duration = 12;
        break;
      case "kitchen":
        if (diff >= 3) duration = 5;
        else if (diff >= 2) duration = 7;
        else if (diff >= 1) duration = 10;
        else duration = 12;
        break;
      case "bedroom":
        if (diff >= 3) duration = 6;
        else if (diff >= 2) duration = 8;
        else if (diff >= 1) duration = 10;
        else duration = 12;
        break;
      case "basement":
        if (diff >= 4) duration = 7;
        else if (diff >= 3) duration = 10;
        else if (diff >= 2) duration = 12;
        else duration = 15;
        break;
      default:
        if (diff >= 4) duration = 5;
        else if (diff >= 3) duration = 7;
        else if (diff >= 2) duration = 10;
        else duration = 12;
        break;
    }

    if (windSpeed !== null) {
      if (windSpeed >= 20) duration -= 2;
      else if (windSpeed >= 12) duration -= 1;
      else if (windSpeed <= 5) duration += 1;
    }

    return Math.max(3, duration);
  }

  getCoolingDuration(coolingDelta, windSpeed = null) {
    let duration;
    if (coolingDelta >= 8) duration = 15;
    else if (coolingDelta >= 6) duration = 12;
    else if (coolingDelta >= 4) duration = 10;
    else duration = 8;

    if (windSpeed !== null) {
      if (windSpeed >= 20) duration -= 2;
      else if (windSpeed >= 12) duration -= 1;
      else if (windSpeed <= 5) duration += 1;
    }

    return Math.max(5, duration);
  }

  calculateScore(room) {
    const scharlau = this.getState(room.scharlau);
    const humidex = this.normalizeHumidexState(this.getState(room.humidex));
    const dewpoint = this.getState(room.dewpoint);
    const simmer = this.getState(room.simmer);
    const temp = this.getNumber(room.temperature);
    const relHumidity = this.getNumber(room.humidity);
    const humidexValue = this.getNumber(room.humidex_value);
    const profile = this.getRoomProfile(room);

    const scharlauScore = {
      comfortable: 100,
      slightly_uncomfortable: 75,
      moderately_uncomfortable: 55,
      highly_uncomfortable: 30,
      outside_calculable_range: 70,
    };

    const humidexScore = {
      comfortable: 100,
      noticeable_discomfort: 65,
      evident_discomfort: 45,
      great_discomfort: 25,
      dangerous_discomfort: 10,
      heat_stroke: 0,
    };

    const dewpointScore = {
      dry: 90,
      very_comfortable: 100,
      comfortable: 95,
      ok_but_humid: 80,
      somewhat_uncomfortable: 60,
      quite_uncomfortable: 35,
      extremely_uncomfortable: 15,
      severely_high: 0,
    };

    const simmerScore = {
      cool: 90,
      slightly_cool: 95,
      comfortable: 100,
      slightly_warm: 78,
      increasing_discomfort: 58,
      extremely_warm: 35,
      danger_of_heatstroke: 15,
      extreme_danger_of_heatstroke: 5,
      circulatory_collapse_imminent: 0,
    };

    let score = Math.round(
      (scharlauScore[scharlau] ?? 70) * 0.25 +
        (humidexScore[humidex] ?? 70) * 0.25 +
        (dewpointScore[dewpoint] ?? 70) * 0.2 +
        (simmerScore[simmer] ?? 70) * 0.3
    );

    if (temp !== null) {
      if (temp >= 32) score -= 40;
      else if (temp >= 30) score -= 28;
      else if (temp >= 28) score -= 18;
      else if (temp >= 26) score -= 10;
      else if (temp <= 15) score -= 20;
      else if (temp <= 17) score -= 10;
    }

    if (humidexValue !== null) {
      if (humidexValue >= 40) score -= 35;
      else if (humidexValue >= 35) score -= 25;
      else if (humidexValue >= 30) score -= 15;
      else if (humidexValue >= 27) score -= 8;
    }

    if (relHumidity !== null) {
      if (relHumidity < Math.max(profile.humidityMin - 5, 35)) score -= 10;
      else if (relHumidity > profile.humidityMax + 10) score -= 18;
      else if (relHumidity > profile.humidityMax + 5) score -= 10;
    }

    return Math.max(0, Math.min(100, score));
  }

  getLevel(score) {
    if (score >= 90) return { icon: "🟢", label: "Ideal", cls: "rc-excellent" };
    if (score >= 75) return { icon: "😊", label: "Angenehm", cls: "rc-good" };
    if (score >= 60) return { icon: "🟡", label: "Okay", cls: "rc-medium" };
    if (score >= 45) return { icon: "🟠", label: "Belastend", cls: "rc-warning" };
    if (score >= 30) return { icon: "🔴", label: "Schlecht", cls: "rc-bad" };
    return { icon: "🚨", label: "Kritisch", cls: "rc-critical" };
  }

  getDisplayLevel(room, score) {
    const baseLevel = this.getLevel(score);
    const simmerState = this.getState(room.simmer);
    const temp = this.getNumber(room.temperature);
    const humidexValue = this.getNumber(room.humidex_value);

    if (
      simmerState === "circulatory_collapse_imminent" ||
      simmerState === "extreme_danger_of_heatstroke" ||
      (temp !== null && temp >= 34) ||
      (humidexValue !== null && humidexValue >= 40)
    ) {
      return { icon: "🚨", label: "Hitzekritisch", cls: "rc-critical" };
    }

    if (
      simmerState === "danger_of_heatstroke" ||
      simmerState === "extremely_warm" ||
      (temp !== null && temp >= 30) ||
      (humidexValue !== null && humidexValue >= 35)
    ) {
      return { icon: "🔴", label: "Stark wärmebelastet", cls: "rc-bad" };
    }

    if (
      simmerState === "increasing_discomfort" ||
      simmerState === "slightly_warm" ||
      (temp !== null && temp >= 28) ||
      (humidexValue !== null && humidexValue >= 30)
    ) {
      if (baseLevel.cls === "rc-excellent" || baseLevel.cls === "rc-good") {
        return { icon: "🟠", label: "Wärmebelastet", cls: "rc-warning" };
      }
    }

    return baseLevel;
  }

  getTempText(temp) {
    if (temp === null) return "nicht bekannt";
    if (temp < 20) return "etwas kühl";
    if (temp < 24) return "angenehm temperiert";
    if (temp < 27) return "etwas warm";
    if (temp < 29) return "warm";
    if (temp < 31) return "sehr warm";
    return "heiß";
  }

  getDewText(value) {
    return (
      {
        dry: "trocken",
        very_comfortable: "sehr angenehm",
        comfortable: "angenehm",
        ok_but_humid: "leicht schwül",
        somewhat_uncomfortable: "schwül",
        quite_uncomfortable: "deutlich schwül",
        extremely_uncomfortable: "sehr schwül",
        severely_high: "extrem schwül",
      }[value] ?? "nicht eindeutig"
    );
  }

  normalizeHumidexState(value) {
    if (value === "noticable_discomfort") return "noticeable_discomfort";
    return value;
  }

  getHumidexText(value) {
    const normalizedValue = this.normalizeHumidexState(value);
    return (
      {
        comfortable: "angenehm",
        noticeable_discomfort: "spürbar unangenehm",
        evident_discomfort: "deutlich unangenehm",
        great_discomfort: "stark belastend",
        dangerous_discomfort: "kritisch belastend",
        heat_stroke: "gefährlich",
      }[normalizedValue] ?? "nicht eindeutig"
    );
  }

  getSimmerText(value) {
    return (
      {
        cool: "kühl",
        slightly_cool: "leicht kühl",
        comfortable: "angenehm",
        slightly_warm: "leicht warm",
        increasing_discomfort: "zunehmend belastend",
        extremely_warm: "sehr warm",
        danger_of_heatstroke: "hitzekritisch",
        extreme_danger_of_heatstroke: "akut hitzekritisch",
        circulatory_collapse_imminent: "kreislaufkritisch",
      }[value] ?? "nicht eindeutig"
    );
  }

  getDescription(room, score, temp, dewText, humidexText) {
    const tempText = this.getTempText(temp);
    const humidexValue = this.getNumber(room.humidex_value);
    const relHumidity = this.getNumber(room.humidity);
    const profile = this.getRoomProfile(room);
    const simmerState = this.getState(room.simmer);
    const simmerText = this.getSimmerText(simmerState);

    if (temp !== null && temp >= 32) {
      return `Im ${room.name} ist es mit ${temp.toFixed(1)} °C stark überhitzt. Auch wenn die Luft ${dewText} wirkt, ist das Raumklima deutlich belastend und fühlt sich ${simmerText} an.`;
    }

    if (temp !== null && temp >= 29) {
      return `Im ${room.name} ist es mit ${temp.toFixed(1)} °C sehr warm. Das Raumklima ist trotz ${dewText}er Luft spürbar belastend und wirkt ${simmerText}.`;
    }

    if (simmerState === "danger_of_heatstroke" || simmerState === "extreme_danger_of_heatstroke" || simmerState === "circulatory_collapse_imminent") {
      return `Im ${room.name} ist die Hitzebelastung kritisch. Der Sommer-Simmer wirkt ${simmerText} und der Humidex ist ${humidexText}.`;
    }

    if (humidexValue !== null && humidexValue >= 35) {
      return `Im ${room.name} ist die Wärmebelastung deutlich erhöht. Der Humidex liegt bei ${humidexValue.toFixed(1)} und fühlt sich ${humidexText} an.`;
    }

    if (
      simmerState === "increasing_discomfort" ||
      simmerState === "slightly_warm" ||
      (humidexValue !== null && humidexValue >= 30) ||
      (temp !== null && temp >= 27)
    ) {
      return `Im ${room.name} ist es aktuell ${tempText}. Das Raumklima wirkt bereits leicht wärmebelastet, auch wenn die Luft ${dewText} erscheint.`;
    }

    if (relHumidity !== null && relHumidity < Math.max(profile.humidityMin - 5, 35)) {
      return `Im ${room.name} ist die Luft aktuell zu trocken (${relHumidity.toFixed(0)} %). Die Temperatur kann dabei trotzdem ${tempText} sein.`;
    }

    if (score >= 90) {
      return `Im ${room.name} herrscht derzeit ein nahezu ideales Raumklima. Temperatur und Luftfeuchtigkeit liegen im Wohlfühlbereich.`;
    }

    if (score >= 75) {
      return `Im ${room.name} ist es aktuell ${tempText}. Die Luft wirkt ${dewText}, insgesamt ist das Raumklima noch recht angenehm.`;
    }

    if (score >= 60) {
      return `Im ${room.name} ist es aktuell ${tempText}. Zusammen mit der ${dewText}en Luft wirkt das Raumklima bereits leicht belastend.`;
    }

    if (score >= 45) {
      return `Im ${room.name} ist das Raumklima spürbar belastend. Es ist ${tempText}, die Luft wirkt ${dewText} und der Humidex ist ${humidexText}.`;
    }

    if (score >= 30) {
      return `Im ${room.name} herrscht eine deutliche Wärmebelastung. Lüften oder Kühlen sollte geprüft werden.`;
    }

    return `Im ${room.name} herrscht aktuell ein kritisches Raumklima. Eine Kühlung ist empfehlenswert.`;
  }

  roomHtml(room) {
    const profile = this.getRoomProfile(room);
    const dehumidifyAdvice = this.calculateDehumidifyAdvice(room);
    const coolingAdvice = this.calculateCoolingAdvice(room);
    const ventilation = this.calculateVentilation(room);
    const nextVentilationWindow = this.getNextVentilationWindowLine(room, coolingAdvice);
    const temp = this.getNumber(room.temperature);
    const hum = this.getNumber(room.humidity);
    const humidexValue = this.getNumber(room.humidex_value);
    const simmerText = this.getSimmerText(this.getState(room.simmer));
    const windowState = this.getState(room.window);
    const score = this.calculateScore(room);
    const level = this.getDisplayLevel(room, score);
    const dewText = this.getDewText(this.getState(room.dewpoint));
    const humidexText = this.getHumidexText(this.getState(room.humidex));
    const description = this.getDescription(room, score, temp, dewText, humidexText);
    const ventilationClass = ventilation ? `vent-${ventilation.level}` : "vent-none";

    const windowText = room.window
      ? ["on", "open", "tilted"].includes(windowState)
        ? "offen"
        : "geschlossen"
      : "kein Sensor";

    if (this.config.mode === "compact") {
      return `
        <div class="room ${level.cls} ${ventilationClass}">
          <div class="top">
            <div>
              <div class="name">${level.icon} ${room.name}</div>
              <div class="sub">${level.label}</div>
            </div>
            <div class="score">${score}</div>
          </div>
          <div class="metrics">
            <span>🌡️ ${temp?.toFixed(1) ?? "-"} °C</span>
            <span>💧 ${hum?.toFixed(0) ?? "-"} %</span>
            ${dehumidifyAdvice ? `<span>💧 ${dehumidifyAdvice.text}</span>` : ""}
            ${coolingAdvice ? `<span>🧊 ${coolingAdvice.text}</span>` : ""}
          </div>
        </div>
      `;
    }

    return `
      <div class="room ${level.cls} ${ventilationClass}">
        <div class="top">
          <div>
            <div class="name">${level.icon} ${room.name}</div>
            <div class="sub">Raumklima: ${score}/100 · ${level.label}</div>
          </div>
          <div class="score">${score}</div>
        </div>

        <div class="text">${description}</div>

        ${(dehumidifyAdvice || coolingAdvice) ? `
          <div class="ventilation-box">
            ${dehumidifyAdvice ? `<div><b>💧 Entfeuchtung:</b> ${dehumidifyAdvice.text}</div>` : ""}
            ${coolingAdvice ? `<div><b>🧊 Abkühlung:</b> ${coolingAdvice.text}</div>` : ""}
            ${nextVentilationWindow ? `<div class="next-window"><b>${nextVentilationWindow}</b></div>` : ""}
          </div>
        ` : ""}

        <div class="details">
          <div><b>🌡️ Temperatur:</b> ${temp?.toFixed(1) ?? "-"} °C</div>
          <div><b>💧 Luftfeuchtigkeit:</b> ${hum?.toFixed(0) ?? "-"} %</div>
          <div><b>🎯 Zielbereich:</b> ${profile.humidityMin}–${profile.humidityMax} % · ${profile.tempMin}–${profile.tempMax} °C</div>
          <div><b>Humidex:</b> ${humidexValue?.toFixed(1) ?? "-"}</div>
          <div><b>Sommer Simmer:</b> ${simmerText}</div>
          <div><b>🪟 Fenster:</b> ${windowText}</div>
        </div>
      </div>
    `;
  }

  groupedHtml() {
    const groups = {};

    this.config.rooms
      .filter((room) => room.enabled !== false)
      .forEach((room) => {
        const score = this.calculateScore(room);
        const level = this.getDisplayLevel(room, score);
        const dehumidifyAdvice = this.calculateDehumidifyAdvice(room);
        const coolingAdvice = this.calculateCoolingAdvice(room);
        const key = `${level.icon} ${level.label}`;
        groups[key] ??= [];
        groups[key].push({ room, score, dehumidifyAdvice, coolingAdvice });
      });

    return `
      <ha-card>
        <div class="content">
          <div class="title">Raumklima Übersicht</div>
          ${Object.entries(groups)
            .map(
              ([label, rooms]) => `
                <div class="group">
                  <div class="group-title">${label}</div>
                  ${rooms
                    .map(
                      ({ room, score, dehumidifyAdvice, coolingAdvice }) => `
                        <div class="group-row">
                          <span>${room.name}</span>
                          <span>${
                            dehumidifyAdvice || coolingAdvice
                              ? [
                                  dehumidifyAdvice ? `💧 ${dehumidifyAdvice.text}` : "",
                                  coolingAdvice ? `🧊 ${coolingAdvice.text}` : "",
                                ].filter(Boolean).join(" · ")
                              : "Kein Fenstersensor"
                          }</span>
                          <b>${score}/100</b>
                        </div>
                      `
                    )
                    .join("")}
                </div>
              `
            )
            .join("")}
        </div>
        ${this.styles()}
      </ha-card>
    `;
  }

  render() {
    if (!this._hass || !this.config) return;

    if (this.config.mode === "grouped") {
      this.innerHTML = this.groupedHtml();
      return;
    }

    const rooms = (this.config.rooms || []).filter((room) => room.enabled !== false);

    this.innerHTML = `
      <ha-card>
        <div class="content">
          <div class="title">Raumklima</div>
          <div class="grid" style="grid-template-columns: repeat(${this.config.columns}, minmax(0, 1fr));">
            ${rooms.map((room) => this.roomHtml(room)).join("")}
          </div>
        </div>
        ${this.styles()}
      </ha-card>
    `;
  }

  styles() {
    return `
      <style>
        .content { padding: 16px; }
        .title { font-size: 20px; font-weight: 700; margin-bottom: 14px; }
        .grid { display: grid; gap: 12px; }
        .room {
          border-radius: 18px;
          padding: 14px;
          border: 1px solid var(--divider-color);
          background: var(--ha-card-background, var(--card-background-color));
        }
        .top {
          display: flex;
          justify-content: space-between;
          gap: 12px;
          align-items: center;
        }
        .name { font-size: 17px; font-weight: 700; }
        .sub { color: var(--secondary-text-color); font-size: 13px; }
        .score { font-size: 28px; font-weight: 800; }
        .text { margin: 12px 0; line-height: 1.4; }
        .details { display: grid; gap: 5px; font-size: 14px; }
        .metrics {
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
          font-size: 13px;
          color: var(--secondary-text-color);
          margin-top: 8px;
        }
        .ventilation-box {
          margin: 10px 0;
          padding: 10px;
          border-radius: 12px;
          background: rgba(127, 127, 127, 0.10);
        }
        .next-window {
          margin-top: 8px;
          padding-top: 8px;
          border-top: 1px solid rgba(255, 255, 255, 0.08);
          font-size: 13px;
        }
        .rc-excellent { border-left: 6px solid #2e7d32; }
        .rc-good { border-left: 6px solid #66bb6a; }
        .rc-medium { border-left: 6px solid #fbc02d; }
        .rc-warning { border-left: 6px solid #fb8c00; }
        .rc-bad { border-left: 6px solid #e53935; }
        .rc-critical { border-left: 6px solid #b71c1c; }
        .vent-recommended .ventilation-box,
        .vent-short .ventilation-box {
          background: rgba(33, 150, 243, 0.14);
        }
        .vent-dry .ventilation-box,
        .vent-avoid .ventilation-box {
          background: rgba(255, 152, 0, 0.14);
        }
        .vent-open .ventilation-box {
          background: rgba(76, 175, 80, 0.14);
        }
        .group { margin-bottom: 14px; }
        .group-title { font-weight: 700; margin-bottom: 6px; }
        .group-row {
          display: grid;
          grid-template-columns: 1fr 2fr auto;
          gap: 8px;
          align-items: center;
          padding: 8px 10px;
          border-radius: 12px;
          background: rgba(127, 127, 127, 0.08);
          margin-bottom: 5px;
        }
        @media (max-width: 700px) {
          .grid { grid-template-columns: 1fr !important; }
          .group-row { grid-template-columns: 1fr; }
        }
      </style>
    `;
  }

  getCardSize() {
    return 4;
  }
}

class RoomClimateCardEditor extends HTMLElement {
  constructor() {
    super();
    this._renderScheduled = false;
    this._editorInitialized = false;
    this._savedScrollTop = null;
    this._expandedRoomIndex = 0;
  }

  static createEmptyRoom() {
    return {
      enabled: true,
      name: "Neuer Raum",
      room_type: "default",
      temperature: "",
      humidity: "",
      inside_absolute_humidity: "",
      outside_absolute_humidity: "",
      humidex_value: "",
      scharlau: "",
      humidex: "",
      simmer: "",
      dewpoint: "",
      window: "",
    };
  }

  static slugify(value) {
    return (value || "")
      .toLowerCase()
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/^_+|_+$/g, "");
  }

  static findGlobalOutsideHumidity(hass) {
    if (!hass) return "";
    const states = Object.keys(hass.states || {});
    return (
      states.find((e) => e.includes("aussen") && e.includes("absolute_luftfeuchtigkeit")) ||
      states.find((e) => e.includes("außen") && e.includes("absolute_luftfeuchtigkeit")) ||
      states.find((e) => e.includes("outside") && e.includes("absolute")) ||
      ""
    );
  }

  static findGlobalWeatherEntity(hass) {
    if (!hass) return "";
    return Object.keys(hass.states || {}).find((entityId) => entityId.startsWith("weather.")) || "";
  }

  static guessRoomType(name) {
    const n = RoomClimateCardEditor.slugify(name);

    if (n.includes("bad") || n.includes("badezimmer")) return "bathroom";
    if (n.includes("kuche") || n.includes("kueche")) return "kitchen";
    if (n.includes("keller")) return "basement";
    if (n.includes("kind")) return "child";
    if (n.includes("schlaf")) return "bedroom";
    if (n.includes("buro") || n.includes("buero")) return "office";
    if (n.includes("wohn") || n.includes("essen")) return "living";

    return "default";
  }

  static detectRooms(hass) {
    if (!hass) return [];

    const states = Object.keys(hass.states || {});
    const areas = hass.areas || {};
    const devices = hass.devices || {};
    const entities = hass.entities || {};

    const roomMap = new Map();

    const ensureRoom = (name) => {
      const key = RoomClimateCardEditor.slugify(name || "raum");
      if (!roomMap.has(key)) {
        roomMap.set(key, {
          ...RoomClimateCardEditor.createEmptyRoom(),
          enabled: false,
          name: name || "Raum",
          room_type: RoomClimateCardEditor.guessRoomType(name || "Raum"),
        });
      }
      return roomMap.get(key);
    };

    const isEntityInArea = (entityId, areaId) => {
      const entityReg = entities[entityId];
      const deviceId = entityReg?.device_id;
      const device = deviceId ? devices[deviceId] : null;
      return entityReg?.area_id === areaId || device?.area_id === areaId;
    };

    const findEntityForArea = (areaId, patterns, domains = ["sensor"]) => {
      return (
        states.find((entityId) => {
          if (!domains.includes(entityId.split(".")[0])) return false;
          if (!isEntityInArea(entityId, areaId)) return false;
          return patterns.every((pattern) => entityId.includes(pattern));
        }) || ""
      );
    };

    const areaRooms = Object.entries(areas)
      .map(([areaId, area]) => {
        const name = area.name;
        return {
          ...RoomClimateCardEditor.createEmptyRoom(),
          enabled: false,
          name,
          room_type: RoomClimateCardEditor.guessRoomType(name),
          temperature: findEntityForArea(areaId, ["temperatur"]) || findEntityForArea(areaId, ["temperature"]),
          humidity: findEntityForArea(areaId, ["luftfeuchtigkeit"]) || findEntityForArea(areaId, ["humidity"]),
          inside_absolute_humidity:
            findEntityForArea(areaId, ["absolute_luftfeuchtigkeit"]) ||
            findEntityForArea(areaId, ["absolute_humidity"]),
          humidex_value: findEntityForArea(areaId, ["thermal_comfort", "humidex"]),
          scharlau: findEntityForArea(areaId, ["thermal_comfort", "sommer_scharlau_gefuhlt"]),
          humidex: findEntityForArea(areaId, ["thermal_comfort", "humidex_gefuhlt"]),
          simmer: findEntityForArea(areaId, ["thermal_comfort", "sommer_simmer_gefuhlt"]),
          dewpoint: findEntityForArea(areaId, ["thermal_comfort", "taupunkt_gefuhlt"]),
          window: findEntityForArea(areaId, ["fenster"], ["binary_sensor"]) || findEntityForArea(areaId, ["window"], ["binary_sensor"]),
        };
      })
      .filter(
        (room) =>
          room.temperature ||
          room.humidity ||
          room.inside_absolute_humidity ||
          room.humidex_value ||
          room.scharlau ||
          room.humidex ||
          room.simmer ||
          room.dewpoint ||
          room.window
      );

    if (areaRooms.length) {
      return areaRooms;
    }

    states.forEach((entityId) => {
      const [domain, objectId] = entityId.split(".");
      if (!["sensor", "binary_sensor"].includes(domain) || !objectId) return;

      const slug = RoomClimateCardEditor.slugify(objectId);
      const parts = slug.split("_").filter(Boolean);
      if (!parts.length) return;

      const ignore = new Set([
        "sensor",
        "thermal",
        "comfort",
        "absolute",
        "luftfeuchtigkeit",
        "humidity",
        "temperatur",
        "temperature",
        "humidex",
        "simmer",
        "taupunkt",
        "gefuhlt",
        "sommer",
        "scharlau",
        "window",
        "fenster",
        "contact",
        "kontakt",
        "value",
      ]);

      const roomParts = parts.filter((part) => !ignore.has(part));
      if (!roomParts.length) return;

      const roomName = roomParts.map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(" ");
      const room = ensureRoom(roomName);

      if (domain === "binary_sensor" && /(fenster|window|kontakt|contact)/.test(slug)) {
        room.window ||= entityId;
      } else if (/(^|_)(temperatur|temperature)($|_)/.test(slug)) {
        room.temperature ||= entityId;
      } else if (/(^|_)(luftfeuchtigkeit|humidity)($|_)/.test(slug) && !/absolute/.test(slug)) {
        room.humidity ||= entityId;
      } else if (/absolute_(luftfeuchtigkeit|humidity)/.test(slug)) {
        room.inside_absolute_humidity ||= entityId;
      } else if (/humidex/.test(slug) && !/gefuhlt/.test(slug)) {
        room.humidex_value ||= entityId;
      } else if (/scharlau/.test(slug)) {
        room.scharlau ||= entityId;
      } else if (/simmer/.test(slug)) {
        room.simmer ||= entityId;
      } else if (/humidex.*gefuhlt|gefuhlt.*humidex/.test(slug)) {
        room.humidex ||= entityId;
      } else if (/taupunkt/.test(slug)) {
        room.dewpoint ||= entityId;
      }
    });

    return [...roomMap.values()].filter(
      (room) =>
        room.temperature ||
        room.humidity ||
        room.inside_absolute_humidity ||
        room.humidex_value ||
        room.scharlau ||
        room.humidex ||
        room.simmer ||
        room.dewpoint ||
        room.window
    );
  }

  setConfig(config) {
    const hadConfig = Boolean(this.config);

    this.config = {
      mode: "detailed",
      columns: 2,
      outside_absolute_humidity: "",
      outside_weather: "",
      rooms: [],
      ...config,
    };

    if (!Array.isArray(this.config.rooms) || this.config.rooms.length === 0) {
      this.config.rooms = [RoomClimateCardEditor.createEmptyRoom()];
    }

    if (this._hass && !hadConfig) {
      this.scheduleRender();
    }
  }

  set hass(hass) {
    this._hass = hass;

    if (!this._editorInitialized) {
      this._editorInitialized = true;
      if (this.config) {
        this.scheduleRender();
      }
      return;
    }

    if (!this.config) {
      this.scheduleRender();
    }
  }

  scheduleRender() {
    if (this._renderScheduled) return;
    this._renderScheduled = true;
    requestAnimationFrame(() => {
      this._renderScheduled = false;
      this.render();
    });
  }

  captureScrollPosition() {
    const scrollParent = this.closest(".content") || this.parentElement || this;
    this._savedScrollTop = scrollParent?.scrollTop ?? null;
  }

  restoreScrollPosition() {
    if (this._savedScrollTop === null) return;
    const scrollTop = this._savedScrollTop;
    this._savedScrollTop = null;

    requestAnimationFrame(() => {
      const scrollParent = this.closest(".content") || this.parentElement || this;
      if (scrollParent) {
        scrollParent.scrollTop = scrollTop;
      }
    });
  }

  escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  entityPicker(index, key, label, value, domains = ["sensor"]) {
    return `
      <label>${label}</label>
      <ha-entity-picker
        data-domains="${this.escapeHtml(domains.join(","))}"
        data-i="${index}"
        data-key="${key}"
        value="${this.escapeHtml(value ?? "")}"
        allow-custom-entity
      ></ha-entity-picker>
    `;
  }

  fireConfigChanged() {
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: { config: this.config },
        bubbles: true,
        composed: true,
      })
    );
  }

  syncRoomHeader(index) {
    const room = this.config.rooms?.[index];
    if (!room) return;

    const roomEditor = this.querySelector(`.room-editor[data-room-index="${index}"]`);
    if (!roomEditor) return;

    const title = roomEditor.querySelector(".room-editor-title");
    const meta = roomEditor.querySelector(".room-editor-meta");

    if (title) {
      title.textContent = room.name || "Raum";
    }

    if (meta) {
      meta.textContent = room.enabled !== false ? "aktiv" : "deaktiviert";
    }
  }

  initializeEntityPickers() {
    this.querySelectorAll("ha-entity-picker").forEach((picker) => {
      picker.hass = this._hass;

      const domains = (picker.dataset.domains || "")
        .split(",")
        .map((value) => value.trim())
        .filter(Boolean);

      if (domains.length) {
        picker.includeDomains = domains;
      }

      if ("allowCustomEntity" in picker) {
        picker.allowCustomEntity = true;
      }
    });
  }

  updateRoot(key, value) {
    this.config = {
      ...this.config,
      [key]: value,
    };

    this.captureScrollPosition();
    this.fireConfigChanged();
    this.restoreScrollPosition();
  }

  updateRoom(index, key, value) {
    const rooms = [...this.config.rooms];
    rooms[index] = {
      ...rooms[index],
      [key]: value,
    };

    this.config = {
      ...this.config,
      rooms,
    };

    this.captureScrollPosition();
    this.fireConfigChanged();
    this.syncRoomHeader(index);
    this.restoreScrollPosition();
  }

  addRoom() {
    this.config = {
      ...this.config,
      rooms: [...this.config.rooms, RoomClimateCardEditor.createEmptyRoom()],
    };
    this._expandedRoomIndex = this.config.rooms.length - 1;

    this.captureScrollPosition();
    this.fireConfigChanged();
    this.scheduleRender();
  }

  removeRoom(index) {
    const rooms = this.config.rooms.filter((_, i) => i !== index);
    this.config = {
      ...this.config,
      rooms: rooms.length ? rooms : [RoomClimateCardEditor.createEmptyRoom()],
    };
    this._expandedRoomIndex = Math.max(0, Math.min(this._expandedRoomIndex, this.config.rooms.length - 1));

    this.captureScrollPosition();
    this.fireConfigChanged();
    this.scheduleRender();
  }

  detectAndAddRooms() {
    const detectedRooms = RoomClimateCardEditor.detectRooms(this._hass);
    this.config = {
      ...this.config,
      outside_absolute_humidity:
        this.config.outside_absolute_humidity || RoomClimateCardEditor.findGlobalOutsideHumidity(this._hass),
      outside_weather: this.config.outside_weather || RoomClimateCardEditor.findGlobalWeatherEntity(this._hass),
      rooms: detectedRooms.length ? detectedRooms : [RoomClimateCardEditor.createEmptyRoom()],
    };
    this._expandedRoomIndex = 0;

    this.captureScrollPosition();
    this.fireConfigChanged();
    this.scheduleRender();
  }

  toggleRoomExpanded(index) {
    this.captureScrollPosition();
    this._expandedRoomIndex = this._expandedRoomIndex === index ? -1 : index;
    this.render();
    this.restoreScrollPosition();
  }

  render() {
    if (!this._hass || !this.config) return;

    const rooms = this.config.rooms?.length ? this.config.rooms : [RoomClimateCardEditor.createEmptyRoom()];

    this.innerHTML = `
      <div class="editor">
        <label>Darstellung</label>
        <select id="mode">
          <option value="detailed" ${this.config.mode === "detailed" ? "selected" : ""}>Detailliert</option>
          <option value="compact" ${this.config.mode === "compact" ? "selected" : ""}>Kompakt</option>
          <option value="grouped" ${this.config.mode === "grouped" ? "selected" : ""}>Gruppiert</option>
        </select>

        <label>Spalten</label>
        <select id="columns">
          <option value="1" ${this.config.columns == 1 ? "selected" : ""}>1</option>
          <option value="2" ${this.config.columns == 2 ? "selected" : ""}>2</option>
          <option value="3" ${this.config.columns == 3 ? "selected" : ""}>3</option>
        </select>

        <label>Absolute Außenluftfeuchtigkeit (global, g/m³)</label>
        <ha-entity-picker
          id="outside_absolute_humidity"
          data-domains="sensor"
          value="${this.escapeHtml(this.config.outside_absolute_humidity ?? "")}"
          allow-custom-entity
        ></ha-entity-picker>

        <label>Wetter-Entity außen (optional für Wind)</label>
        <ha-entity-picker
          id="outside_weather"
          data-domains="weather"
          value="${this.escapeHtml(this.config.outside_weather ?? "")}"
          allow-custom-entity
        ></ha-entity-picker>

        <div class="button-row">
          <button id="detect" type="button">Räume automatisch erkennen</button>
          <button id="add-room" type="button" class="secondary">Raum hinzufügen</button>
        </div>

        <details class="entity-help">
          <summary>Hinweis zur Auswahl</summary>
          <div class="entity-help-grid">
            <div>
              <b>Raumsensoren</b>
              <div>Relative Luftfeuchtigkeit und Temperatur kommen vom Basis-Sensor im Raum.</div>
            </div>
            <div>
              <b>Thermal Comfort</b>
              <div>Absolute Luftfeuchtigkeit, Humidex und Komfortwerte kommen aus thermal_comfort.</div>
            </div>
          </div>
        </details>

        <h3>Räume</h3>

        ${rooms
          .map(
            (room, i) => {
              const isExpanded = this._expandedRoomIndex === i || (this._expandedRoomIndex === 0 && i === 0);
              return `
              <div class="room-editor ${isExpanded ? "expanded" : ""}" data-room-index="${i}">
                <button type="button" class="room-editor-header room-toggle" data-i="${i}">
                  <span class="room-editor-title">${this.escapeHtml(room.name || "Raum")}</span>
                  <span class="room-editor-meta">${room.enabled !== false ? "aktiv" : "deaktiviert"}</span>
                </button>
                ${isExpanded ? `
                <div class="room-editor-body">
                  <div class="inline-actions">
                    <label class="toggle">
                      <input type="checkbox" data-i="${i}" data-key="enabled" ${room.enabled !== false ? "checked" : ""}>
                      Raum aktiv
                    </label>
                    <button type="button" class="danger remove-room" data-i="${i}">Entfernen</button>
                  </div>

                  <label>Name</label>
                  <input type="text" data-i="${i}" data-key="name" value="${this.escapeHtml(room.name ?? "")}">

                  <label>Raumtyp</label>
                  <select data-i="${i}" data-key="room_type">
                    <option value="default" ${room.room_type === "default" ? "selected" : ""}>Standard</option>
                    <option value="living" ${room.room_type === "living" ? "selected" : ""}>Wohnraum</option>
                    <option value="bedroom" ${room.room_type === "bedroom" ? "selected" : ""}>Schlafzimmer</option>
                    <option value="child" ${room.room_type === "child" ? "selected" : ""}>Kinderzimmer</option>
                    <option value="bathroom" ${room.room_type === "bathroom" ? "selected" : ""}>Badezimmer</option>
                    <option value="kitchen" ${room.room_type === "kitchen" ? "selected" : ""}>Küche</option>
                    <option value="basement" ${room.room_type === "basement" ? "selected" : ""}>Keller</option>
                    <option value="office" ${room.room_type === "office" ? "selected" : ""}>Büro</option>
                  </select>

                  ${this.entityPicker(i, "temperature", "Temperatur im Raum (°C, Basis-Sensor)", room.temperature)}
                  ${this.entityPicker(i, "humidity", "Relative Luftfeuchtigkeit im Raum (%, Basis-Sensor)", room.humidity)}
                  ${this.entityPicker(i, "inside_absolute_humidity", "Absolute Innenluftfeuchtigkeit im Raum (g/m³, thermal_comfort)", room.inside_absolute_humidity)}
                  ${this.entityPicker(i, "window", "Fenstersensor", room.window, ["binary_sensor"])}
                  ${this.entityPicker(i, "humidex_value", "Humidex Wert (thermal_comfort)", room.humidex_value)}
                  ${this.entityPicker(i, "scharlau", "Sommer Scharlau gefühlt (thermal_comfort)", room.scharlau)}
                  ${this.entityPicker(i, "humidex", "Humidex gefühlt (thermal_comfort)", room.humidex)}
                  ${this.entityPicker(i, "simmer", "Sommer Simmer gefühlt (thermal_comfort)", room.simmer)}
                  ${this.entityPicker(i, "dewpoint", "Taupunkt gefühlt (thermal_comfort)", room.dewpoint)}
                </div>
                ` : ""}
              </div>
            `;
            }
          )
          .join("")}
      </div>

      <style>
        .editor {
          display: grid;
          gap: 10px;
          padding: 8px 0;
        }

        label,
        h3 {
          font-weight: 600;
        }

        select,
        input {
          width: 100%;
          box-sizing: border-box;
          padding: 8px;
          border-radius: 8px;
          border: 1px solid var(--divider-color);
          background: var(--card-background-color);
          color: var(--primary-text-color);
        }

        button {
          padding: 10px;
          border-radius: 10px;
          border: 1px solid var(--divider-color);
          background: var(--primary-color);
          color: white;
          font-weight: 700;
          cursor: pointer;
        }

        button.secondary {
          background: transparent;
          color: var(--primary-text-color);
        }

        button.danger {
          background: #b71c1c;
        }

        .button-row,
        .inline-actions {
          display: flex;
          gap: 8px;
          align-items: center;
          justify-content: space-between;
        }

        .entity-help {
          border: 1px solid var(--divider-color);
          border-radius: 12px;
          padding: 10px 12px;
          background: rgba(127, 127, 127, 0.06);
        }

        .entity-help summary {
          cursor: pointer;
          font-weight: 600;
        }

        .entity-help-grid {
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 8px;
          margin-top: 8px;
          font-size: 13px;
          color: var(--secondary-text-color);
        }

        .room-editor {
          border: 1px solid var(--divider-color);
          border-radius: 14px;
          background: rgba(127, 127, 127, 0.04);
        }

        .room-editor-header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 8px;
          padding: 12px;
          cursor: pointer;
          width: 100%;
          background: transparent;
          color: inherit;
          border: 0;
        }

        .room-editor-title {
          font-weight: 700;
        }

        .room-editor-meta {
          font-size: 12px;
          color: var(--secondary-text-color);
          text-transform: uppercase;
          letter-spacing: 0.04em;
        }

        .room-editor-body {
          display: grid;
          gap: 8px;
          padding: 0 12px 12px;
        }

        .toggle {
          display: flex;
          align-items: center;
          gap: 8px;
          font-size: 16px;
        }

        .toggle input {
          width: auto;
        }

        @media (max-width: 700px) {
          .button-row,
          .inline-actions,
          .entity-help-grid {
            grid-template-columns: 1fr;
            display: grid;
          }
        }
      </style>
    `;

    this.querySelector("#mode")?.addEventListener("change", (e) => {
      this.updateRoot("mode", e.target.value);
    });

    this.querySelector("#columns")?.addEventListener("change", (e) => {
      this.updateRoot("columns", Number(e.target.value));
    });

    this.querySelector("#outside_absolute_humidity")?.addEventListener("change", (e) => {
      this.updateRoot("outside_absolute_humidity", e.target.value);
    });

    this.querySelector("#outside_absolute_humidity")?.addEventListener("value-changed", (e) => {
      this.updateRoot("outside_absolute_humidity", e.detail?.value ?? "");
    });

    this.querySelector("#outside_weather")?.addEventListener("value-changed", (e) => {
      this.updateRoot("outside_weather", e.detail?.value ?? "");
    });

    this.querySelector("#detect")?.addEventListener("click", () => {
      this.detectAndAddRooms();
    });

    this.querySelector("#add-room")?.addEventListener("click", () => {
      this.addRoom();
    });

    this.querySelectorAll(".room-toggle").forEach((button) => {
      button.addEventListener("click", (e) => {
        this.toggleRoomExpanded(Number(e.currentTarget.dataset.i));
      });
    });

    this.querySelectorAll(".remove-room").forEach((button) => {
      button.addEventListener("click", (e) => {
        this.removeRoom(Number(e.currentTarget.dataset.i));
      });
    });

    this.querySelectorAll("input[type='text'][data-i]").forEach((input) => {
      input.addEventListener("change", (e) => {
        this.updateRoom(Number(e.target.dataset.i), e.target.dataset.key, e.target.value);
      });
    });

    this.querySelectorAll("input[type='checkbox'][data-i]").forEach((input) => {
      input.addEventListener("change", (e) => {
        this.updateRoom(Number(e.target.dataset.i), e.target.dataset.key, e.target.checked);
      });
    });

    this.querySelectorAll("select[data-i]").forEach((select) => {
      select.addEventListener("change", (e) => {
        this.updateRoom(Number(e.target.dataset.i), e.target.dataset.key, e.target.value);
      });
    });

    this.querySelectorAll("ha-entity-picker[data-i]").forEach((picker) => {
      picker.addEventListener("value-changed", (e) => {
        this.updateRoom(Number(e.currentTarget.dataset.i), e.currentTarget.dataset.key, e.detail?.value ?? "");
      });
    });

    this.initializeEntityPickers();
  }
}

customElements.define("room-climate-card", RoomClimateCard);
customElements.define("room-climate-card-editor", RoomClimateCardEditor);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "room-climate-card",
  name: "Room Climate Card",
  description: "Raumklima Card mit Score, Zielwerten, Lüftungsempfehlung, Fenstersensoren und UI-Auswahl",
});
