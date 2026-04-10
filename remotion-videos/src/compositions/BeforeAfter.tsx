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

interface BeforeAfterProps {
  beforeLabel: string;
  beforeStats: string[];
  afterLabel: string;
  afterStats: string[];
  savingsStat: string;
}

const Panel: React.FC<{
  label: string;
  stats: string[];
  accentColor: string;
  opacity: number;
  translateX: number;
}> = ({ label, stats, accentColor, opacity, translateX }) => (
  <div
    style={{
      opacity,
      transform: `translateX(${translateX}px)`,
      backgroundColor: accentColor + "18",
      border: `2px solid ${accentColor}`,
      borderRadius: 20,
      padding: 40,
      marginBottom: 24,
    }}
  >
    <div
      style={{
        color: accentColor,
        fontSize: 28,
        fontWeight: 700,
        textTransform: "uppercase",
        letterSpacing: "2px",
        marginBottom: 24,
      }}
    >
      {label}
    </div>
    {stats.map((s, i) => (
      <div
        key={i}
        style={{
          color: CSK.white,
          fontSize: CSK.fontBody,
          fontWeight: 600,
          marginBottom: 12,
          paddingLeft: 16,
          borderLeft: `3px solid ${accentColor}`,
        }}
      >
        {s}
      </div>
    ))}
  </div>
);

export const BeforeAfter: React.FC<BeforeAfterProps> = ({
  beforeLabel,
  beforeStats,
  afterLabel,
  afterStats,
  savingsStat,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // BEFORE panel slides in from left: 0-60f
  const beforeSpring = spring({
    frame,
    fps,
    config: { damping: 18, stiffness: 100, mass: 1 },
  });
  const beforeX = interpolate(beforeSpring, [0, 1], [-300, 0]);
  const beforeOpacity = interpolate(frame, [0, 30], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // AFTER panel slides in from right: 60-120f
  const afterSpring = spring({
    frame: frame - 60,
    fps,
    config: { damping: 18, stiffness: 100, mass: 1 },
  });
  const afterX = interpolate(afterSpring, [0, 1], [300, 0]);
  const afterOpacity = interpolate(frame, [60, 90], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // Savings stat counts up: 120-180f
  const savingsOpacity = interpolate(frame, [120, 160], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // CTA appears: 180-240f
  const ctaOpacity = interpolate(frame, [180, 220], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill
      style={{
        backgroundColor: CSK.bg,
        fontFamily: CSK.fontFamily,
      }}
    >
      <SafeZone>
        {/* BEFORE panel */}
        <Panel
          label={beforeLabel}
          stats={beforeStats}
          accentColor="#EF4444"
          opacity={beforeOpacity}
          translateX={beforeX}
        />

        {/* AFTER panel */}
        <Panel
          label={afterLabel}
          stats={afterStats}
          accentColor={CSK.accent}
          opacity={afterOpacity}
          translateX={afterX}
        />

        {/* Savings stat */}
        <div
          style={{
            opacity: savingsOpacity,
            textAlign: "center",
            marginTop: 32,
          }}
        >
          <div
            style={{
              color: CSK.accent,
              fontSize: 96,
              fontWeight: 800,
              lineHeight: 1,
            }}
          >
            {savingsStat}
          </div>
        </div>

        {/* CTA */}
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
          }}
        >
          csktech.solutions
        </div>
      </SafeZone>
    </AbsoluteFill>
  );
};
