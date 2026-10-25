import { createFileRoute } from "@tanstack/react-router";
import { JarvisHUD } from "@/components/jarvis/JarvisHUD";

export const Route = createFileRoute("/")({
  ssr: false,
  head: () => ({
    meta: [
      { title: "J.A.R.V.I.S — Mark VII Holographic Assistant" },
      { name: "description", content: "A futuristic 3D Stark-style holographic assistant interface." },
      { property: "og:title", content: "J.A.R.V.I.S — Mark VII Holographic Assistant" },
      { property: "og:description", content: "A futuristic 3D Stark-style holographic assistant interface." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: JarvisHUD,
});
