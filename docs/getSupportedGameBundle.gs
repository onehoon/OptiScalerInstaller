/**
 * OptiScaler GPU bundle endpoint
 *
 * Sheets:
 * - install_profile
 * - optiscaler_ini_profile
 *
 * Script Property:
 * - OPTISCALER_PROFILE_SPREADSHEET_ID
 */

function doGet(e) {
  var action = normalizeText_(e && e.parameter && e.parameter.action).toLowerCase();
  if (action !== "getsupportedgamebundle") {
    return jsonOutput_({
      ok: false,
      error: "Unsupported action"
    });
  }

  var vendor = normalizeText_(e && e.parameter && e.parameter.vendor).toLowerCase();
  var gpu = normalizeText_(e && e.parameter && e.parameter.gpu);

  try {
    var result = getSupportedGameBundle_(vendor, gpu);
    return jsonOutput_({
      ok: true,
      games: result.games,
      profiles: result.profiles
    });
  } catch (err) {
    return jsonOutput_({
      ok: false,
      error: err && err.message ? err.message : String(err)
    });
  }
}

function getSupportedGameBundle_(vendor, gpu) {
  var spreadsheet = resolveSpreadsheet_();

  var installSheet = spreadsheet.getSheetByName("install_profile");
  if (!installSheet) {
    throw new Error("install_profile sheet not found");
  }

  var installValues = installSheet.getDataRange().getValues();
  var selectedByGameId = selectInstallProfilesFromValues_(installValues, vendor, gpu);
  var gameIds = Object.keys(selectedByGameId).sort();

  // Exact-match profile ids for per-game profile sheets.
  var activeProfiles = new Set();
  for (var i = 0; i < gameIds.length; i++) {
    activeProfiles.add(normalizeProfileIdKey_(selectedByGameId[gameIds[i]].profile_id));
  }

  // Layered profile ids for OptiScaler.ini only.
  var optiscalerIniLayerProfiles = buildOptiscalerIniLayerProfiles_(selectedByGameId, vendor);

  var sharedOptiscalerIni = buildProfileRowListFromSheet_(
    spreadsheet,
    "optiscaler_ini_profile",
    optiscalerIniLayerProfiles,
    ["profile_id", "section", "key", "value", "priority", "enabled", "memo"]
  );

  var bundle = [];
  for (var j = 0; j < gameIds.length; j++) {
    var gameId = gameIds[j];
    var profile = selectedByGameId[gameId];
    var profileId = normalizeText_(profile.profile_id);

    bundle.push({
      game_id: normalizeText_(profile.game_id),
      profile_id: profileId,
      install_profile: {
        profile_id: profileId,
        optiscaler_dll_name: normalizeText_(profile.optiscaler_dll_name),
        ultimate_asi_loader: parseBool_(profile.ultimate_asi_loader, false),
        optipatcher: parseBool_(profile.optipatcher, false),
        specialk: parseBool_(profile.specialk, false),
        reframework_url: normalizeOptionalText_(profile.reframework_url),
        unreal5: parseBool_(profile.unreal5, false),
        rtss_overlay: parseBool_(profile.rtss_overlay, false),
        enabled: parseBool_(profile.enabled, true)
      }
    });
  }

  return {
    games: bundle,
    profiles: {
      optiscaler_ini: sharedOptiscalerIni
    }
  };
}

/**
 * install_profile values array -> game_id별 최종 프로파일 선택
 */
function selectInstallProfilesFromValues_(values, vendor, gpu) {
  if (!values || values.length < 2) {
    return {};
  }

  var headers = values[0].map(normalizeHeader_);
  var idx = {
    enabled: headers.indexOf("enabled"),
    game_id: headers.indexOf("game_id"),
    vendor: headers.indexOf("gpu_vendor"),
    model: headers.indexOf("gpu_model_match"),
    priority: headers.indexOf("priority")
  };

  var selected = {};

  for (var i = 1; i < values.length; i++) {
    var row = values[i];
    if (idx.enabled > -1 && !parseBool_(row[idx.enabled], true)) {
      continue;
    }

    var gameId = normalizeText_(row[idx.game_id]);
    if (!gameId) {
      continue;
    }

    var rowVendor = normalizeText_(row[idx.vendor]).toLowerCase();
    if (rowVendor && rowVendor !== "all" && rowVendor !== vendor) {
      continue;
    }

    var modelRule = normalizeText_(row[idx.model]);
    if (modelRule && !wildcardMatch_(gpu, modelRule)) {
      continue;
    }

    var candidate = {
      rowValues: row,
      priority: parsePriority_(row[idx.priority], 100),
      specificity: modelRule ? 1 : 0,
      index: i
    };

    var existing = selected[gameId];
    if (
      !existing ||
      candidate.specificity > existing.specificity ||
      (candidate.specificity === existing.specificity && candidate.priority < existing.priority) ||
      (candidate.specificity === existing.specificity && candidate.priority === existing.priority && candidate.index < existing.index)
    ) {
      selected[gameId] = candidate;
    }
  }

  var result = {};
  for (var selectedGameId in selected) {
    var item = {};
    var rowData = selected[selectedGameId].rowValues;
    for (var k = 0; k < headers.length; k++) {
      item[headers[k]] = rowData[k];
    }
    result[selectedGameId] = item;
  }

  return result;
}

/**
 * OptiScaler.ini 레이어용 profile id 집합 생성
 * - GLOBAL_ALL
 * - GLOBAL_<VENDOR>
 * - <GAME_ID>_ALL
 * - <PROFILE_ID>
 */
function buildOptiscalerIniLayerProfiles_(selectedByGameId, vendor) {
  var ids = new Set();
  ids.add(normalizeProfileIdKey_("GLOBAL_ALL"));

  var normalizedVendor = normalizeText_(vendor).toUpperCase();
  if (normalizedVendor && normalizedVendor !== "ALL" && normalizedVendor !== "DEFAULT") {
    ids.add(normalizeProfileIdKey_("GLOBAL_" + normalizedVendor));
  }

  var gameIds = Object.keys(selectedByGameId);
  for (var i = 0; i < gameIds.length; i++) {
    var selected = selectedByGameId[gameIds[i]];
    var gameId = normalizeText_(selected.game_id).toUpperCase();
    var profileId = normalizeText_(selected.profile_id);

    if (gameId) {
      ids.add(normalizeProfileIdKey_(gameId + "_ALL"));
    }
    if (profileId) {
      ids.add(normalizeProfileIdKey_(profileId));
    }
  }

  return ids;
}

/**
 * optiscaler_ini_profile용 raw row list
 * GLOBAL + GAME_ALL + PROFILE_ID 레이어를 그대로 내려줌
 */
function buildProfileRowListFromSheet_(spreadsheet, sheetName, activeProfiles, allowedKeys) {
  var sheet = spreadsheet.getSheetByName(sheetName);
  if (!sheet) return [];

  var values = sheet.getDataRange().getValues();
  if (!values || values.length < 2) return [];

  var headers = values[0].map(normalizeHeader_);
  var profileIdIdx = headers.indexOf("profile_id");
  var enabledIdx = headers.indexOf("enabled");

  var colMap = [];
  for (var k = 0; k < allowedKeys.length; k++) {
    var foundIdx = headers.indexOf(allowedKeys[k]);
    if (foundIdx > -1) {
      colMap.push({ name: allowedKeys[k], idx: foundIdx });
    }
  }

  var rows = [];
  for (var i = 1; i < values.length; i++) {
    var row = values[i];

    if (enabledIdx > -1 && !parseBool_(row[enabledIdx], true)) {
      continue;
    }

    var profileId = normalizeText_(row[profileIdIdx]);
    var profileKey = normalizeProfileIdKey_(profileId);
    if (!profileKey || !activeProfiles.has(profileKey)) {
      continue;
    }

    var item = {};
    for (var j = 0; j < colMap.length; j++) {
      var keyName = colMap[j].name;
      var rawValue = row[colMap[j].idx];
      item[keyName] = keyName === "value" ? normalizeProfileValue_(rawValue) : rawValue;
    }

    rows.push(item);
  }

  return rows;
}

// ------------------------------------------------------------------
// Common utilities
// ------------------------------------------------------------------

function resolveSpreadsheet_() {
  var spreadsheetId = PropertiesService.getScriptProperties().getProperty("OPTISCALER_PROFILE_SPREADSHEET_ID");
  if (spreadsheetId) {
    return SpreadsheetApp.openById(spreadsheetId);
  }

  var activeSpreadsheet = SpreadsheetApp.getActiveSpreadsheet();
  if (activeSpreadsheet) {
    return activeSpreadsheet;
  }

  throw new Error("Spreadsheet not configured.");
}

function wildcardMatch_(text, pattern) {
  var normalizedPattern = normalizeText_(pattern);
  if (!normalizedPattern) return true;

  var parts = normalizedPattern.split("|");
  for (var i = 0; i < parts.length; i++) {
    var part = normalizeText_(parts[i]);
    if (!part) continue;
    if (wildcardMatchSingle_(text, part)) {
      return true;
    }
  }

  return false;
}

function wildcardMatchSingle_(text, pattern) {
  var t = normalizeText_(text).toLowerCase();
  var p = normalizeText_(pattern).toLowerCase();

  if (!p || p === "*" || p === "all") {
    return true;
  }

  var regexText = "^" + p
    .replace(/[.+^${}()|[\]\\]/g, "\\$&")
    .replace(/\*/g, ".*")
    .replace(/\?/g, ".") + "$";

  return new RegExp(regexText, "i").test(t);
}

function parseBool_(value, defaultValue) {
  if (typeof value === "boolean") {
    return value;
  }

  var normalized = normalizeText_(value).toLowerCase();
  if (!normalized) {
    return !!defaultValue;
  }
  if (["1", "true", "yes", "y", "on"].indexOf(normalized) !== -1) {
    return true;
  }
  if (["0", "false", "no", "n", "off"].indexOf(normalized) !== -1) {
    return false;
  }
  return !!defaultValue;
}

function parsePriority_(value, defaultValue) {
  var normalized = normalizeText_(value);
  if (!normalized) {
    return defaultValue;
  }
  var parsed = parseInt(normalized, 10);
  return isNaN(parsed) ? defaultValue : parsed;
}

function normalizeHeader_(value) {
  return normalizeText_(value).toLowerCase().replace(/\s+/g, "_");
}

function normalizeText_(value) {
  return value === null || value === undefined ? "" : String(value).trim();
}

function normalizeOptionalText_(value) {
  var text = normalizeText_(value);
  var lowered = text.toLowerCase();
  if (text === '""' || text === "''" || lowered === "false") {
    return "";
  }
  return text;
}

function normalizeProfileIdKey_(value) {
  return normalizeText_(value).toUpperCase();
}

function normalizeProfileValue_(value) {
  if (value === null || value === undefined) {
    return "";
  }
  if (typeof value === "boolean") {
    return value ? "true" : "false";
  }
  return String(value).trim();
}

function jsonOutput_(payload) {
  return ContentService
    .createTextOutput(JSON.stringify(payload))
    .setMimeType(ContentService.MimeType.JSON);
}
