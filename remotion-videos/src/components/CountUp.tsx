import React from "react";
import { useCurrentFrame, interpolate } from "remotion";

interface CountUpProps {
  from: number;
  to: number;
  startFrame: number;
  endFrame: number;
  prefix?: string;
  suffix?: string;
  decimals?: number;
  style?: React.CSSProperties;
}

export const CountUp: React.FC<CountUpProps> = ({
  from,
  to,
  startFrame,
  endFrame,
  prefix = "",
  suffix = "",
  decimals = 0,
  style,
}) => {
  const frame = useCurrentFrame();
  const value = interpolate(frame, [startFrame, endFrame], [from, to], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const display =
    decimals > 0
      ? value.toFixed(decimals)
      : Math.round(value).toLocaleString();

  return (
    <span style={{ fontVariantNumeric: "tabular-nums", ...style }}>
      {prefix}
      {display}
      {suffix}
    </span>
  );
};
