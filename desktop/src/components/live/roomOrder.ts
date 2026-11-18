import type { LiveMonitor } from "../../services/liveMonitoring";

export const roomOrderKey = "douyincut.live-room-order";

export function readRoomOrder(): string[] {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(roomOrderKey) || "[]");
    return Array.isArray(saved) ? [...new Set(saved.filter((id): id is string => typeof id === "string"))] : [];
  } catch {
    return [];
  }
}

export function sortRooms(monitors: LiveMonitor[], order: string[]): LiveMonitor[] {
  const ranks = new Map(order.map((id, index) => [id, index]));
  return [...monitors].sort((first, second) => {
    const firstRank = ranks.get(first.id);
    const secondRank = ranks.get(second.id);
    if (firstRank !== undefined || secondRank !== undefined) return (firstRank ?? Infinity) - (secondRank ?? Infinity);
    return Number(second.is_live === true) - Number(first.is_live === true) || first.name.localeCompare(second.name, "zh-CN");
  });
}
