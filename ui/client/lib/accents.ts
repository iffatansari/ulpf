export const ACCENT = {
  blue: "#2f8ce0",
  blueBright: "#91C8FF",
  blueMist: "#E8F4FF",
  lilac: "#7c4dcc",
  lilacBright: "#B9A7FF",
  lilacMist: "#F0ECFF",
  mint: "#0f766e",
  mintBright: "#BDEDE3",
  mintMist: "#E2F7F2",
  amber: "#d97706",
  amberBright: "#FBBF24",
  amberMist: "#FDE9B8",
  rose: "#e11d48",
  roseBright: "#FB7185",
  roseMist: "#FFE1E6",
  emerald: "#059669",
  emeraldMist: "#D5F5EB",
  gray: "#72748A",
  grayMist: "#E4E8F2",
  ink: "#25263A",
} as const;

export interface Accent {
  strong: string;
  tint: string;
}

// OCSF category_uid -> accent, used to tint class badges consistently everywhere.
export const CATEGORY_TONE: Record<number, Accent> = {
  0: { strong: ACCENT.gray, tint: ACCENT.grayMist },
  1: { strong: ACCENT.amber, tint: ACCENT.amberMist }, // System Activity
  2: { strong: ACCENT.rose, tint: ACCENT.roseMist }, // Findings
  3: { strong: ACCENT.lilac, tint: ACCENT.lilacMist }, // IAM
  4: { strong: ACCENT.blue, tint: ACCENT.blueMist }, // Network Activity
  5: { strong: ACCENT.emerald, tint: ACCENT.emeraldMist }, // Discovery
  6: { strong: ACCENT.mint, tint: ACCENT.mintMist }, // Application Activity
};

export function categoryTone(categoryUid: number | undefined): Accent {
  return CATEGORY_TONE[categoryUid ?? 0] ?? CATEGORY_TONE[0];
}