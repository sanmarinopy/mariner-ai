"""Datos reales de la galaxia de Elite Dangerous desde EDSM (https://www.edsm.net).

API pública de lectura: no necesita cuenta ni clave. Los datos los aporta la comunidad de
jugadores, por eso cada respuesta incluye la fecha de actualización cuando EDSM la informa.
Se guarda una caché de 10 minutos para no repetir consultas (y respetar los límites de EDSM).
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import Any

import aiohttp

log = logging.getLogger("mariner.edsm")
BASE = "https://www.edsm.net"
CACHE_S = 600
KEY_SERVICES = ["Refuel", "Repair", "Restock", "Material Trader", "Technology Broker", "Interstellar Factors",
                "Universal Cartographics", "Black Market", "Search and Rescue", "Tuning"]


class EDSMError(Exception):
    pass


class EDSM:
    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._cache: dict[str, tuple[float, Any]] = {}
        self._sem = asyncio.Semaphore(2)

    async def _get(self, path: str, **params: Any) -> Any:
        params = {k: v for k, v in params.items() if v is not None}
        key = path + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < CACHE_S:
            return hit[1]
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=8), headers={"User-Agent": "Mariner-AI/0.1"})
        async with self._sem:
            try:
                async with self._session.get(BASE + path, params=params) as r:
                    if r.status == 429:
                        raise EDSMError("EDSM está limitando las consultas; reintenta en un minuto")
                    if r.status != 200:
                        raise EDSMError(f"EDSM respondió {r.status}")
                    data = await r.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                raise EDSMError(f"No pude contactar a EDSM ({type(e).__name__})") from e
        self._cache[key] = (time.monotonic(), data)
        return data

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    # ------------------------------------------------------------------ consultas
    async def system(self, name: str) -> dict[str, Any]:
        d = await self._get("/api-v1/system", systemName=name, showInformation=1, showPrimaryStar=1,
                            showCoordinates=1, showPermit=1)
        if not d:
            raise EDSMError(f"EDSM no conoce el sistema '{name}'")
        info, star = d.get("information") or {}, d.get("primaryStar") or {}
        return {
            "sistema": d.get("name"),
            "coordenadas": d.get("coords"),
            "requiere_permiso": d.get("requirePermit", False),
            "permiso": d.get("permitName"),
            "alianza": info.get("allegiance"),
            "gobierno": info.get("government"),
            "faccion_controladora": info.get("faction"),
            "estado_faccion": info.get("factionState"),
            "poblacion": info.get("population"),
            "seguridad": info.get("security"),
            "economia": [e for e in (info.get("economy"), info.get("secondEconomy")) if e],
            "estrella_principal": star.get("type"),
            "estrella_recargable": star.get("isScoopable"),
            "habitado": bool(info.get("population")),
        }

    async def stations(self, system: str) -> dict[str, Any]:
        d = await self._get("/api-system-v1/stations", systemName=system)
        sts = sorted(d.get("stations") or [], key=lambda s: s.get("distanceToArrival") or 1e12)
        if not d or not d.get("name"):
            raise EDSMError(f"EDSM no conoce el sistema '{system}'")
        out = []
        for s in sts[:12]:
            services = [x for x in (s.get("otherServices") or []) if x in KEY_SERVICES]
            if s.get("haveMarket"):
                services.append("Mercado")
            if s.get("haveShipyard"):
                services.append("Astillero")
            if s.get("haveOutfitting"):
                services.append("Equipamiento")
            out.append({
                "nombre": s.get("name"), "tipo": s.get("type"),
                "distancia_ls": s.get("distanceToArrival"),
                "plataforma_grande": None if not s.get("type") else ("Outpost" not in s["type"]),
                "economia": s.get("economy"), "servicios": services,
                "faccion": (s.get("controllingFaction") or {}).get("name"),
                "actualizado": (s.get("updateTime") or {}).get("information"),
            })
        return {"sistema": d.get("name"), "total_estaciones": len(sts), "estaciones": out,
                "nota": "Los puestos avanzados (Outpost) no tienen plataformas grandes."}

    async def bodies(self, system: str) -> dict[str, Any]:
        d = await self._get("/api-system-v1/bodies", systemName=system)
        if not d or not d.get("name"):
            raise EDSMError(f"EDSM no conoce el sistema '{system}'")
        bs = d.get("bodies") or []
        stars = [f"{b.get('name')} ({b.get('subType')})" for b in bs if b.get("type") == "Star"]
        notable_types = ("Earth-like world", "Water world", "Ammonia world")
        notable = [f"{b.get('name')}: {b.get('subType')}" for b in bs if b.get("subType") in notable_types]
        terra = [b.get("name") for b in bs if b.get("terraformingState") == "Candidate for terraforming"]
        landable = sorted([b for b in bs if b.get("isLandable")], key=lambda b: b.get("distanceToArrival") or 1e12)
        return {
            "sistema": d.get("name"),
            "cuerpos_conocidos": len(bs), "cuerpos_totales": d.get("bodyCount"),
            "estrellas": stars[:6],
            "mundos_valiosos": notable[:8],
            "terraformables": terra[:8],
            "aterrizables": [{"nombre": b.get("name"), "tipo": b.get("subType"),
                              "gravedad_g": round(b.get("gravity") or 0, 2),
                              "distancia_ls": b.get("distanceToArrival")} for b in landable[:10]],
        }

    async def market(self, system: str, station: str, commodity: str | None = None) -> dict[str, Any]:
        d = await self._get("/api-system-v1/stations/market", systemName=system, stationName=station)
        comms = d.get("commodities") if d else None
        if not comms:
            raise EDSMError(f"EDSM no tiene mercado para '{station}' en '{system}'")
        row = lambda c: {"producto": c["name"], "compra": c.get("buyPrice"), "stock": c.get("stock"),  # noqa: E731
                         "venta": c.get("sellPrice"), "demanda": c.get("demand")}
        res: dict[str, Any] = {"estacion": d.get("sName"), "sistema": d.get("name")}
        if commodity:
            q = commodity.lower()
            match = [row(c) for c in comms if q in c["name"].lower() or q in c.get("id", "")]
            res["resultado"] = match[:5] or f"'{commodity}' no figura en este mercado"
        else:
            sells = sorted([c for c in comms if c.get("demand")], key=lambda c: -c.get("sellPrice", 0))
            buys = sorted([c for c in comms if c.get("stock")], key=lambda c: -c.get("buyPrice", 0))
            res["mejor_para_vender_aqui"] = [row(c) for c in sells[:8]]
            res["disponible_para_comprar"] = [row(c) for c in buys[:8]]
        res["nota"] = "Precios en créditos; datos aportados por jugadores, pueden tener algunas horas."
        return res

    async def nearby(self, system: str, radius: float = 20, populated: bool = False,
                     scoopable: bool = False, limit: int = 10) -> dict[str, Any]:
        radius = max(1, min(float(radius), 50))
        d = await self._get("/api-v1/sphere-systems", systemName=system, radius=radius,
                            showInformation=1, showPrimaryStar=1)
        if not isinstance(d, list):
            raise EDSMError(f"EDSM no conoce el sistema '{system}'")
        seen, out = set(), []
        for s in sorted(d, key=lambda s: s.get("distance", 1e9)):
            if s.get("name") in seen or s.get("distance", 0) == 0:
                continue
            seen.add(s.get("name"))
            info, star = s.get("information") or {}, s.get("primaryStar") or {}
            if populated and not info.get("population"):
                continue
            if scoopable and not star.get("isScoopable"):
                continue
            out.append({"sistema": s.get("name"), "distancia_ly": s.get("distance"),
                        "estrella": star.get("type"), "recargable": star.get("isScoopable"),
                        "poblacion": info.get("population"), "economia": info.get("economy"),
                        "seguridad": info.get("security")})
            if len(out) >= limit:
                break
        return {"origen": system, "radio_ly": radius, "sistemas": out}

    async def distance(self, a: str, b: str) -> dict[str, Any]:
        sa, sb = await asyncio.gather(self.system(a), self.system(b))
        ca, cb = sa.get("coordenadas"), sb.get("coordenadas")
        if not ca or not cb:
            raise EDSMError("EDSM no tiene coordenadas de alguno de los sistemas")
        ly = math.dist((ca["x"], ca["y"], ca["z"]), (cb["x"], cb["y"], cb["z"]))
        return {"desde": sa["sistema"], "hasta": sb["sistema"], "distancia_ly": round(ly, 2)}


def _tool(name: str, desc: str, props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props, **({"required": required} if required else {})}}}


_SYS = {"system": {"type": "string", "description": "Nombre del sistema. Vacío = sistema actual de la nave."}}

TOOLS = [
    _tool("galaxy_system", "Datos reales de un sistema estelar (EDSM): facción, gobierno, población, "
          "seguridad, economía, estrella principal y si es recargable, permisos.", _SYS),
    _tool("galaxy_stations", "Estaciones de un sistema (EDSM): tipo, distancia, servicios, plataformas.", _SYS),
    _tool("galaxy_bodies", "Cuerpos de un sistema (EDSM): estrellas, mundos valiosos, terraformables, "
          "planetas aterrizables.", _SYS),
    _tool("galaxy_market", "Precios del mercado de una estación (EDSM). Con 'commodity' busca un producto.",
          {**_SYS, "station": {"type": "string"}, "commodity": {"type": "string"}}, ["station"]),
    _tool("galaxy_nearby", "Sistemas cercanos (EDSM), del más cercano al más lejano. Sirve para buscar "
          "estrellas recargables o sistemas habitados cerca.",
          {**_SYS, "radius_ly": {"type": "number", "description": "Radio en años luz, máx. 50"},
           "populated_only": {"type": "boolean"}, "scoopable_only": {"type": "boolean"}}),
    _tool("galaxy_distance", "Distancia en años luz entre dos sistemas (EDSM).",
          {"from_system": {"type": "string", "description": "Vacío = sistema actual"},
           "to_system": {"type": "string"}}, ["to_system"]),
]
TOOL_NAMES = {t["function"]["name"] for t in TOOLS}


async def call(edsm: EDSM, name: str, args: dict[str, Any], current_system: str | None) -> dict[str, Any]:
    def sysname(key: str = "system") -> str:
        v = (args.get(key) or "").strip() or (current_system or "")
        if not v:
            raise EDSMError("Falta el nombre del sistema (no hay telemetría para saber dónde estamos)")
        return v

    if name == "galaxy_system":
        return await edsm.system(sysname())
    if name == "galaxy_stations":
        return await edsm.stations(sysname())
    if name == "galaxy_bodies":
        return await edsm.bodies(sysname())
    if name == "galaxy_market":
        return await edsm.market(sysname(), args["station"], args.get("commodity"))
    if name == "galaxy_nearby":
        return await edsm.nearby(sysname(), args.get("radius_ly") or 20,
                                 bool(args.get("populated_only")), bool(args.get("scoopable_only")))
    if name == "galaxy_distance":
        return await edsm.distance(sysname("from_system"), args["to_system"])
    raise EDSMError(f"Herramienta desconocida: {name}")
