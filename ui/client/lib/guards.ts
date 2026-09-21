import type { LineResult, NormalizedEventResult, RejectedEventResult } from "@shared/api";

export const isEvent = (l: LineResult): l is NormalizedEventResult => l.ok === true;

export const isRejected = (l: LineResult): l is RejectedEventResult => l.ok === false;