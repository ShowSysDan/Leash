--[[
  Leash NDI Control — QSYS Integration Script
  ============================================
  Drop this into a QSYS Scriptable Controls component.

  Configuration:
    LEASH_URL  : base URL of the Leash server (no trailing slash)
    API_KEY    : match the API_KEY in Leash .env, or "" if auth is disabled
    SUBNET     : the fixed IP prefix for your BirdDog PLAY network

  How it works:
    • RouteByIndex(ipOctet, sourceIndex)  — route by stable source number
    • RouteByName(ipOctet, sourceName)    — route by exact source name string
    • BulkRoute(routes)                   — route a table of {octet, source} pairs
    • GetSources()                        — fetch full source list into Controls
    • GetReceivers()                      — fetch all receiver statuses

  Source indices never change even if a source goes offline, so you can
  hard-code them in show files without fear of them shifting.
]]

local LEASH_URL = "http://10.1.248.1:5000"   -- change to your Leash server IP
local API_KEY   = ""                           -- set if API_KEY is configured in Leash
local json      = require("json")

-- ─── HTTP helpers ───────────────────────────────────────────────────────────

local function headers()
  local h = {
    ["Content-Type"] = "application/json",
    ["Accept"]       = "application/json",
  }
  if API_KEY ~= "" then
    h["X-API-Key"] = API_KEY
  end
  return h
end

local function GET(path, callback)
  HttpClient.Download({
    Url         = LEASH_URL .. path,
    Headers     = headers(),
    EventHandler = function(tbl, code, data, err)
      if code == 200 then
        local ok, parsed = pcall(json.decode, data)
        if ok and callback then callback(parsed) end
      else
        print(string.format("[Leash] GET %s failed: %s %s", path, tostring(code), tostring(err)))
      end
    end
  })
end

local function POST(path, body, callback)
  HttpClient.Upload({
    Url         = LEASH_URL .. path,
    Method      = "POST",
    Headers     = headers(),
    Data        = json.encode(body),
    EventHandler = function(tbl, code, data, err)
      if code == 200 then
        local ok, parsed = pcall(json.decode, data)
        if ok and callback then callback(parsed) end
      else
        print(string.format("[Leash] POST %s failed: %s %s", path, tostring(code), tostring(err)))
      end
    end
  })
end

-- ─── Public API ─────────────────────────────────────────────────────────────

--[[
  Route a single receiver by stable source index.
  ipOctet     : string or number — last octet of receiver IP (e.g. "83")
  sourceIndex : integer          — stable source index from Leash
]]
function RouteByIndex(ipOctet, sourceIndex)
  print(string.format("[Leash] Route .%s → source #%d", tostring(ipOctet), sourceIndex))
  POST("/api/v1/route", {
    ip_octet = tostring(ipOctet),
    source   = sourceIndex,            -- integer → Leash resolves to name
  }, function(result)
    if result.ok then
      print(string.format("[Leash] ✓ .%s now showing: %s", ipOctet, result.source_name))
    else
      print(string.format("[Leash] ✗ .%s route failed (HTTP %d)", ipOctet, result.http_status or 0))
    end
  end)
end

--[[
  Route a single receiver by source name string.
]]
function RouteByName(ipOctet, sourceName)
  print(string.format("[Leash] Route .%s → '%s'", tostring(ipOctet), sourceName))
  POST("/api/v1/route", {
    ip_octet = tostring(ipOctet),
    source   = sourceName,
  }, function(result)
    if result.ok then
      print(string.format("[Leash] ✓ .%s → %s", ipOctet, sourceName))
    else
      print(string.format("[Leash] ✗ .%s route failed (HTTP %d)", ipOctet, result.http_status or 0))
    end
  end)
end

--[[
  Route multiple receivers at once (concurrent on the Leash server side).
  routes : array of { ip_octet, source } tables
  Example:
    BulkRoute({
      { ip_octet = "83", source = 4  },
      { ip_octet = "84", source = "Camera 2 (NDI)" },
      { ip_octet = "85", source = 1  },
    })
]]
function BulkRoute(routes)
  print(string.format("[Leash] BulkRoute: %d receivers", #routes))
  POST("/api/v1/route/bulk", routes, function(result)
    print(string.format("[Leash] BulkRoute done: %d/%d succeeded",
      result.succeeded or 0, result.attempted or 0))
    if result.errors and #result.errors > 0 then
      for _, e in ipairs(result.errors) do
        print("[Leash] Error: " .. (e.error or "unknown"))
      end
    end
  end)
end

--[[
  Fetch the full source list (online + offline) and store in a table.
  Calls callback(sourcesTable) where sourcesTable[index] = name.
]]
function GetSources(callback)
  GET("/api/v1/sources", function(data)
    local t = {}
    for _, s in ipairs(data.sources or {}) do
      t[s.source_index] = {
        name   = s.name,
        online = s.online,
        index  = s.source_index,
      }
    end
    print(string.format("[Leash] %d sources (%d online)",
      data.count or 0, data.online_count or 0))
    if callback then callback(t) end
  end)
end

--[[
  Populate QSYS combo-box Controls with the online source list.
  controlName : name of a ComboBox named control
]]
function PopulateSourceCombo(controlName)
  GET("/api/v1/sources/online", function(data)
    local names = {}
    for _, s in ipairs(data.sources or {}) do
      -- Format: "#4 Camera East" so operators see the index
      table.insert(names, string.format("#%d %s", s.source_index, s.name))
    end
    table.sort(names)
    if Controls[controlName] then
      Controls[controlName].Choices = names
    end
    print(string.format("[Leash] Populated %s with %d sources", controlName, #names))
  end)
end

--[[
  Fetch all receiver statuses.
  Calls callback(receiversTable) where receiversTable[ip_octet] = {hostname, status, source}.
]]
function GetReceivers(callback)
  GET("/api/v1/receivers", function(data)
    local t = {}
    for _, r in ipairs(data.receivers or {}) do
      t[r.ip_last_octet] = {
        hostname       = r.hostname,
        status         = r.status,
        current_source = r.current_source,
        source_index   = r.current_source_index,
      }
    end
    if callback then callback(t) end
  end)
end

-- ─── Example event handlers ─────────────────────────────────────────────────
-- Wire these up to your QSYS component events.

-- Example: a "Reload Sources" button
-- Controls["ReloadSources"].EventHandler = function()
--   PopulateSourceCombo("SourceSelector")
-- end

-- Example: route receiver .83 to whatever is selected in a combo
-- Controls["SetSource"].EventHandler = function()
--   local val = Controls["SetSource"].String
--   -- val is like "#4 Camera East" — extract the index
--   local idx = tonumber(val:match("^#(%d+)"))
--   if idx then
--     RouteByIndex("83", idx)
--   end
-- end

-- ─── Startup ─────────────────────────────────────────────────────────────────
print("[Leash] Integration script loaded.")
-- GetSources()   -- uncomment to fetch source list on script start
