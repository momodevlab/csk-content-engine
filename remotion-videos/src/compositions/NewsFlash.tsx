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

interface NewsFlashProps {
  headline: string;
  source: string;
  implications: [string, string, string];
  cskContext: string;
}

const TypedText: React.FC<{
  text: string;
  startFrame: number;
  endFrame: number;
  style?: React.CSSProperties;
}> = ({ text, startFrame, endFrame, style }) => {
  const frame = useCurrentFrame();
  const charsToShow = Math.floor(
    interpolate(frame, [startFrame, endFrame], [0, text.length], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
    })
  );
  return <span style={style}>{text.slice(0, charsToShow)}</span>;
};

export const NewsFlash: React.FC<NewsFlashProps> = ({
  headline,
  source,
  implications,
  cskContext,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // BREAKING badge slams in: 0-30f with spring from 3x scale
  const badgeSpring = spring({
    frame,
    fps,
    config: { damping: 10, stiffness: 200, mass: 0.6 },
  });
  const badgeScale = interpolate(badgeSpring, [0, 1], [3, 1]);
  const badgeOpacity = interpolate(frame, [0, 15], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // Implication items stagger in: 70-180f
  const implOpacity = (index: number) =>
    interpolate(frame, [100 + index * 30, 140 + index * 30], [0, 1], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
    });
  const implY = (index: number) => {
    const s = spring({
      frame: frame - (100 + index * 30),
      fps,
      config: { damping: 14, stiffness: 120 },
    });
    return interpolate(s, [0, 1], [40, 0]);
  };

  // CSK context line: 160-300f
  const cskOpacity = interpolate(frame, [160, 220], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // CTA + source: 280-450f
  const ctaOpacity = interpolate(frame, [280, 340], [0, 1], {
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
        {/* BREAKING badge */}
        <div
          style={{
            opacity: badgeOpacity,
            transform: `scale(${badgeScale})`,
            transformOrigin: "left center",
            display: "inline-block",
            backgroundColor: "#EF4444",
            color: CSK.white,
            fontSize: 26,
            fontWeight: 800,
            letterSpacing: "3px",
            paddingTop: 8,
            paddingBottom: 8,
            paddingLeft: 20,
            paddingRight: 20,
            borderRadius: 6,
            marginBottom: 36,
            marginTop: 20,
          }}
        >
          BREAKING
        </div>

        {/* Headline types in: 20-80f */}
        <div
          style={{
            color: CSK.white,
            fontSize: CSK.fontHeadline,
            fontWeight: 700,
            lineHeight: 1.2,
            marginBottom: 48,
          }}
        >
          <TypedText text={headline} startFrame={20} endFrame={80} />
        </div>

        {/* Implications */}
        {implications.map((impl, i) => (
          <div
            key={i}
            style={{
              opacity: implOpacity(i),
              transform: `translateY(${implY(i)}px)`,
              display: "flex",
              alignItems: "flex-start",
              marginBottom: 24,
              gap: 16,
            }}
          >
            <div
              style={{
                color: CSK.accent,
                fontSize: 28,
                fontWeight: 800,
                minWidth: 32,
                marginTop: 2,
              }}
            >
              {i + 1}.
            </div>
            <div
              style={{
                color: CSK.white,
                fontSize: CSK.fontBody,
                fontWeight: 500,
                lineHeight: 1.35,
              }}
            >
              {impl}
            </div>
          </div>
        ))}

        {/* CSK context */}
        <div
          style={{
            opacity: cskOpacity,
            backgroundColor: CSK.accent + "22",
            border: `2px solid ${CSK.accent}`,
            borderRadius: 12,
            padding: 24,
            marginTop: 16,
            color: CSK.accent,
            fontSize: CSK.fontBody,
            fontWeight: 600,
            lineHeight: 1.35,
          }}
        >
          {cskContext}
        </div>

        {/* CTA + source */}
        <div
          style={{
            opacity: ctaOpacity,
            position: "absolute",
            bottom: 0,
            left: 0,
            right: 0,
            textAlign: "center",
          }}
        >
          <div
            style={{
              color: CSK.textMuted,
              fontSize: CSK.fontLabel,
              marginBottom: 8,
            }}
          >
            Source: {source}
          </div>
          <div
            style={{
              color: CSK.accent,
              fontSize: CSK.fontLabel,
              fontWeight: 600,
            }}
          >
            csktech.solutions
          </div>
        </div>
      </SafeZone>
    </AbsoluteFill>
  );
};
