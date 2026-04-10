import React from "react";
import { CSK } from "../constants";

export const SafeZone: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div
    style={{
      position: "absolute",
      top: CSK.safeTop,
      bottom: CSK.safeBottom,
      left: CSK.safeSide,
      right: CSK.safeSide,
      overflow: "hidden",
    }}
  >
    {children}
  </div>
);
