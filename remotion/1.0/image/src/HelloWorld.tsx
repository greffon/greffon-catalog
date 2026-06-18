import { AbsoluteFill, interpolate, useCurrentFrame } from "remotion";

export const HelloWorld: React.FC<{ title: string }> = ({ title }) => {
  const frame = useCurrentFrame();
  const opacity = interpolate(frame, [0, 30], [0, 1], {
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill
      style={{
        backgroundColor: "#0b0b0f",
        justifyContent: "center",
        alignItems: "center",
      }}
    >
      <h1
        style={{
          color: "white",
          fontFamily: "sans-serif",
          fontSize: 80,
          opacity,
        }}
      >
        {title}
      </h1>
    </AbsoluteFill>
  );
};
