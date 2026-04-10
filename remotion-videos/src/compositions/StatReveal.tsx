import React from "react";
import {
  useCurrentFrame,
  useVideoConfig,
  interpolate,
  spring,
  AbsoluteFill,
} from "remotion";
import { CSK } from "../constants";
import { SafeZone } from "../components/SafeZone";
import { CountUp } from "../components/CountUp";

interface StatRevealProps {
  stat: number;
  statPrefix?: string;
  statSuffix?: string;
  contextLine: string;
  insightLine: string;
  ctaLine?: string;
}

export const StatReveal: React.FC<StatRevealProps> = ({
  stat,
  statPrefix = "",
  statSuffix = "",
  contextLine,
  insightLine,
  ctaLine = "csktech.solutions",
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Background fade in: 0-15f
  const bgOpacity = interpolate(frame, [0, 15], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // Context label slides up: 10-40f
  const contextY = spring({
    frame: frame - 10,
    fps,
    config: { damping: 14, stiffness: 120, mass: 0.8 },
  });
  const contextOpacity = interpolate(frame, [10, 30], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const contextTranslateY = interpolate(contextY, [0, 1], [60, 0]);

  // Stat suffix fades in: 80-120f
  const suffixOpacity = interpolate(frame, [80, 120], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // Insight line fades in: 100-150f
  const insightOpacity = interpolate(frame, [100, 140], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // CTA fades in: 120-150f
  const ctaOpacity = interpolate(frame, [120, 150], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill
      style={{
        backgroundColor: CSK.bg,
        opacity: bgOpacity,
        fontFamily: CSK.fontFamily,
      }}
    >
      <SafeZone>
        {/* Context label */}
        <div
          style={{
            opacity: contextOpacity,
            transform: `translateY(${contextTranslateY}px)`,
            color: CSK.textMuted,
            fontSize: CSK.fontBody,
            fontWeight: 500,
            marginTop: 80,
            lineHeight: 1.3,
          }}
        >
          {contextLine}
        </div>

        {/* Giant stat number */}
        <div
          style={{
            marginTop: 40,
            lineHeight: 1,
          }}
        >
          <CountUp
            from={0}
            to={stat}
            startFrame={30}
            endFrame={100}
            prefix={statPrefix}
            style={{
              color: CSK.accent,
              fontSize: 180,
              fontWeight: 800,
              letterSpacing: "-4px",
            }}
          />
        </div>

        {/* Suffix / unit */}
        <div
          style={{
            opacity: suffixOpacity,
            color: CSK.accent,
            fontSize: 60,
            fontWeight: 700,
            marginTop: 8,
          }}
        >
          {statSuffix}
        </div>

        {/* Insight line */}
        <div
          style={{
            opacity: insightOpacity,
            color: CSK.white,
            fontSize: CSK.fontHeadline,
            fontWeight: 600,
            marginTop: 48,
            lineHeight: 1.25,
            maxWidth: "90%",
          }}
        >
          {insightLine}
        </div>

        {/* CTA at bottom */}
        <div
          style={{
            opacity: ctaOpacity,
            position: "absolute",
            bottom: 0,
            left: 0,
            right: 0,
            textAlign: "center",
            color: CSK.textMuted,
            fontSize: CSK.fontLabel,
            fontWeight: 500,
            letterSpacing: "1px",
          }}
        >
          {ctaLine}
        </div>
      </SafeZone>
    </AbsoluteFill>
  );
};
