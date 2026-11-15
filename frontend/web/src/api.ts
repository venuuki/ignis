export type Row = Record<string, any>;
export async function get<T = any>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`/api/${path}`, { signal });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
    throw new Error(detail || `Backend request failed (${response.status})`);
  }
  return response.json();
}
export function params(values: Record<string, string | number>) {
  return new URLSearchParams(Object.entries(values).map(([k, v]) => [k, String(v)])).toString();
}
