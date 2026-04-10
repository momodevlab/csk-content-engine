import "./index.css";
import React from "react";
import { Composition } from "remotion";
import { StatReveal } from "./compositions/StatReveal";
import { BeforeAfter } from "./compositions/BeforeAfter";
import { NewsFlash } from "./compositions/NewsFlash";
import { CSK } from "./constants";

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="StatReveal"
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        component={StatReveal as any}
        durationInFrames={150}
        fps={CSK.fps}
        width={CSK.width}
        height={CSK.height}
        defaultProps={{
          stat: 40,
          statSuffix: " hrs",
          contextLine: "Accounting firms spend",
          insightLine: "every month on manual data entry",
          ctaLine: "csktech.solutions",
        }}
      />
      <Composition
        id="BeforeAfter"
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        component={BeforeAfter as any}
        durationInFrames={240}
        fps={CSK.fps}
        width={CSK.width}
        height={CSK.height}
        defaultProps={{
          beforeLabel: "Manual process",
          beforeStats: ["40 hrs/month", "12% error rate"],
          afterLabel: "With CSK automation",
          afterStats: ["2 hrs/month", "0% errors"],
          savingsStat: "95% time saved",
        }}
      />
      <Composition
        id="NewsFlash"
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        component={NewsFlash as any}
        durationInFrames={450}
        fps={CSK.fps}
        width={CSK.width}
        height={CSK.height}
        defaultProps={{
          headline: "OpenAI releases new enterprise reasoning model",
          source: "VentureBeat",
          implications: [
            "Better document analysis for accounting firms",
            "Lower cost enterprise AI for startups",
            "New automation opportunities for agencies",
          ],
          cskContext: "We're already integrating this for clients.",
        }}
      />
    </>
  );
};
