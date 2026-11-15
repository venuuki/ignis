import { useEffect, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import mapWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
maplibregl.setWorkerUrl(mapWorkerUrl);
import "maplibre-gl/dist/maplibre-gl.css";
import {
  TerraDraw,
  TerraDrawRectangleMode,
  TerraDrawPolygonMode,
  TerraDrawCircleMode,
  TerraDrawSelectMode,
} from "terra-draw";
import { TerraDrawMapLibreGLAdapter } from "terra-draw-maplibre-gl-adapter";
import {
  Plus,
  Minus,
  Maximize,
  MousePointer2,
  Square,
  Hexagon,
  Circle,
  Layers,
  Flame,
} from "lucide-react";

export default function Map({
  region,
  onArea,
  onGeometry,
  exactDrawing = false,
  selectionGeometry,
  records,
  observed = false,
  bounds,
  allowDrawing = true,
}: {
  region: any;
  onArea: (b: number[]) => void;
  onGeometry?: (g: any) => void;
  exactDrawing?: boolean;
  selectionGeometry?: any;
  records?: any[];
  observed?: boolean;
  bounds?: number[];
  allowDrawing?: boolean;
}) {
  const el = useRef<HTMLDivElement>(null),
    map = useRef<maplibregl.Map | null>(null),
    draw = useRef<TerraDraw | null>(null);
  const [ready, setReady] = useState(false);
  const [basemap, setBasemap] = useState("osm");
  const [tileError, setTileError] = useState("");
  const [tool, setTool] = useState("select"),
    [error, setError] = useState(false),
    [layers, setLayers] = useState(false),
    [sensor, setSensor] = useState("All sensors");
  const recordRef = useRef(records);
  recordRef.current = records;
  const callback = useRef(onArea);
  callback.current = onArea;
  const geometryCallback = useRef(onGeometry);
  geometryCallback.current = onGeometry;
  useEffect(() => {
    if (!el.current) return;
    let active = true;
    setReady(false);
    let m: maplibregl.Map;
    try { m = new maplibregl.Map({
      container: el.current,
      style: {
        version: 8,
        sources: {
          world: { type: "geojson", data: "/maps/world.geojson", attribution: "Natural Earth (public domain)" },
          osm: {
            type: "raster",
            tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
            tileSize: 256, minzoom: 0, maxzoom: 19,
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap contributors</a>',
          },
        },
        layers: [
          { id: "ocean", type: "background", paint: { "background-color": "#e7f0f3" } },
          { id: "land", type: "fill", source: "world", paint: { "fill-color": "#f5f5ef" } },
          { id: "borders", type: "line", source: "world", paint: { "line-color": "#bccbc9", "line-width": 0.7 } },
          { id: "osm-streets", type: "raster", source: "osm", layout: { visibility: basemap === "osm" ? "visible" : "none" } },
        ],
      },
      center: region.center,
      zoom: region.zoom,
      maxBounds: [[110,-46],[156,-8]],
      attributionControl: { compact: false },
    }); } catch { setError(true); return; }
    map.current = m;
    const resize = new ResizeObserver(() => m.resize());
    resize.observe(el.current);
    m.on("error", (event: any) => {
      if (!active) return;
      if (event.sourceId === "osm") {
        m.setLayoutProperty("osm-streets", "visibility", "none");
        setBasemap("offline");
        setTileError("Street map unavailable. Showing the local Natural Earth map; detections and selection remain available.");
      } else {
        setError(true);
      }
    });
    m.on("load", () => {
      if (!active) return;
      setError(false);
      m.addSource("hotspots", {
        type: "geojson",
        data: {
          type: "FeatureCollection",
          features: (recordRef.current ?? []).map((p: any) => ({
            type: "Feature",
            geometry: { type: "Point", coordinates: [p.lon, p.lat] },
            properties: p,
          })),
        } as any,
      });
      m.addLayer({
        id: "hotspots-glow",
        type: "circle",
        source: "hotspots",
        paint: {
          "circle-radius": 8,
          "circle-color": "#e8743b",
          "circle-opacity": 0.12,
        },
      });
      m.addLayer({
        id: "hotspots",
        type: "circle",
        source: "hotspots",
        paint: {
          "circle-radius": 3.2,
          "circle-color": "#d76532",
          "circle-stroke-color": "#fff",
          "circle-stroke-width": 1,
        },
      });
      m.on("click", "hotspots", (e) => {
        const p = e.features?.[0]?.properties;
        if (p) {
          const content = document.createElement("div");
          const title = document.createElement("strong");
          title.textContent = `Observed ${String(p.sensor).toUpperCase()} detection`;
          content.append(title);
          for (const line of [
            p.satellite ? `NASA FIRMS · ${p.satellite}` : "NASA FIRMS · selected regional query",
            p.date ? `${p.date} ${p.time || ""}` : "Raw returned point location",
            p.frp != null ? `FRP: ${p.frp} MW` : null,
            p.sensor_confidence != null ? `Native sensor confidence: ${p.sensor_confidence}` : null,
          ].filter(Boolean)) {
            content.append(document.createElement("br"));
            content.append(document.createTextNode(String(line ?? "")));
          }
          new maplibregl.Popup()
            .setLngLat(e.lngLat)
            .setDOMContent(content)
            .addTo(m);
        }
      });
      m.addSource("selection", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      m.addLayer({ id: "selection-fill", type: "fill", source: "selection", paint: { "fill-color": "#3396ac", "fill-opacity": 0.08 } });
      m.addLayer({ id: "selection-line", type: "line", source: "selection", paint: { "line-color": "#236c7e", "line-width": 2 } });
      setReady(true);
      if (!allowDrawing) return;
      const d = new TerraDraw({
        adapter: new TerraDrawMapLibreGLAdapter({ map: m }),
        modes: [
          new TerraDrawSelectMode(),
          new TerraDrawRectangleMode(),
          new TerraDrawPolygonMode(),
          new TerraDrawCircleMode(),
        ],
      });
      d.start();
      d.setMode("select");
      setReady(true);
      draw.current = d;
      d.on("finish", () => {
        const f = d.getSnapshot().at(-1);
        if (!f || f.geometry.type !== "Polygon") return;
        const pts = f.geometry.coordinates[0];
        if (exactDrawing) geometryCallback.current?.(f.geometry);
        // Legacy recent requests continue to use the enclosing rectangle.
        callback.current([
          Math.min(...pts.map((p) => p[0])),
          Math.min(...pts.map((p) => p[1])),
          Math.max(...pts.map((p) => p[0])),
          Math.max(...pts.map((p) => p[1])),
        ]);
        d.setMode("select");
        setTool("select");
      });
    });
    return () => {
      active = false;
      resize.disconnect();
      draw.current?.stop();
      draw.current = null;
      m.remove();
      map.current = null;
    };
  }, [region.id, observed, allowDrawing, exactDrawing]);
  useEffect(() => {
    const m = map.current;
    if (!ready || !m || !bounds) return;
    const [w,s,e,n] = bounds;
    if (![w,s,e,n].every(Number.isFinite) || w >= e || s >= n) return;
    m.fitBounds([[w,s],[e,n]], { padding: 35, maxZoom: 9, duration: 450 });
    const source = m.getSource("selection") as maplibregl.GeoJSONSource | undefined;
    source?.setData({ type: "Feature", properties: {}, geometry: selectionGeometry || { type: "Polygon", coordinates: [[[w,s],[e,s],[e,n],[w,n],[w,s]]] } });
  }, [bounds, ready, selectionGeometry]);
  useEffect(() => {
    const m = map.current;
    const update = () => {
      const src = m?.getSource("hotspots") as
        | maplibregl.GeoJSONSource
        | undefined;
      if (src && records)
        src.setData({
          type: "FeatureCollection",
          features: records.map((p: any) => ({
            type: "Feature",
            geometry: { type: "Point", coordinates: [p.lon, p.lat] },
            properties: p,
          })),
        } as any);
    };
    if (m?.isStyleLoaded()) update();
    else m?.once("load", update);
    return () => {
      m?.off("load", update);
    };
  }, [records]);
  useEffect(() => {
    const m = map.current;
    if (m?.getLayer("hotspots")) {
      const f: any =
        sensor === "All sensors" ? null : ["==", ["get", "sensor"], sensor.toLowerCase()];
      m.setFilter("hotspots", f);
      m.setFilter("hotspots-glow", f);
    }
  }, [sensor, ready]);
  useEffect(() => {
    const m = map.current;
    if (ready && m?.getLayer("osm-streets"))
      m.setLayoutProperty("osm-streets", "visibility", basemap === "osm" ? "visible" : "none");
  }, [basemap, ready]);
  function select(t: string) {
    draw.current?.setMode(t);
    setTool(t);
  }
  return (
    <div className="map-wrap">
      <div ref={el} className="map-canvas" />
      <div className="map-top">
        <span className="map-label">
          <span className="live-dot" /> Satellite detections{" "}
          <span className="mini-badge">{"NASA FIRMS"}</span>
        </span>
        <button
          className="icon-button"
          title="Map layers and basemap"
          onClick={() => setLayers(!layers)}
        >
          <Layers size={17} />
        </button>
      </div>
      {allowDrawing && <div className="map-tools">
        {[
          ["select", MousePointer2, "Pan map"],
          ["rectangle", Square, "Draw rectangle"],
          ["polygon", Hexagon, "Draw polygon"],
          ["circle", Circle, "Draw circle"],
        ].map(([id, I, label]: any) => (
          <button
            key={id}
            title={ready ? label : "Map is loading"}
            disabled={!ready}
            aria-label={label}
            className={tool === id ? "selected" : ""}
            onClick={() => select(id)}
          >
            <I size={17} />
          </button>
        ))}
      </div>}
      <div className="map-zoom">
        <button aria-label="Zoom in" onClick={() => map.current?.zoomIn()}>
          <Plus size={17} />
        </button>
        <button aria-label="Zoom out" onClick={() => map.current?.zoomOut()}>
          <Minus size={17} />
        </button>
        <button
          aria-label="Reset map"
          onClick={() =>
            map.current?.flyTo({ center: region.center, zoom: region.zoom })
          }
        >
          <Maximize size={16} />
        </button>
      </div>
      {layers && (
        <div className="map-layers">
          <strong>Map layers</strong>
          <label>Background map
            <select aria-label="Background map" value={basemap} onChange={(e) => { setBasemap(e.target.value); setTileError(""); }}>
              <option value="osm">OpenStreetMap · online</option>
              <option value="offline">Natural Earth · offline</option>
            </select>
          </label>
          <small>Street detail needs internet. The local map remains available offline.</small>
          <select
            aria-label="Satellite filter"
            value={sensor}
            onChange={(e) => setSensor(e.target.value)}
          >
            <option>All sensors</option>
            <option>VIIRS</option>
            <option>MODIS</option>
          </select>
          <small>
            {observed
              ? "Observed detections · selected window"
              : "Raw returned detections"}
          </small>
        </div>
      )}
      {tileError && <div className="map-error" role="status">{tileError}</div>}
      {error && (
        <div className="map-error">
          Map unavailable (WebGL or local geometry). Coordinate forms and data tables remain available.
        </div>
      )}
      <div className="map-bottom">
        <span>
          <Flame size={12} />{" "}
          {observed ? "Observed detections" : "Observed hotspots"}
        </span>
        <span>{allowDrawing ? "Draw an area to explore →" : "Saved observations · UTC"}</span>
      </div>
    </div>
  );
}
