import type { Metadata } from "next";
import { Alexandria, Noto_Serif, JetBrains_Mono, Archivo } from "next/font/google";
import { ThemeProvider } from "next-themes";
import { Header } from "@/components/Header";
import "./globals.css";

// Fonts match the design tokens in globals.css (--font-sans/--font-serif/
// --font-mono/--font-display): Alexandria for body copy, Noto Serif for
// editorial copy, JetBrains Mono for monospace treatments, Archivo as the
// closest Google Fonts neo-grotesque to the (commercial, unlicensed here)
// Resist Sans requested for the hero's eyebrow + headline.
const fontSans = Alexandria({
  variable: "--font-sans",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
});

const fontSerif = Noto_Serif({
  variable: "--font-serif",
  subsets: ["latin"],
  weight: ["600", "700"],
});

const fontMono = JetBrains_Mono({
  variable: "--font-mono",
  subsets: ["latin"],
});

const fontDisplay = Archivo({
  variable: "--font-display",
  subsets: ["latin"],
  weight: ["600", "700"],
});

export const metadata: Metadata = {
  title: {
    default: "RADAR",
    template: "%s · RADAR",
  },
  description:
    "RADAR scans the automotive aftermarket for competitor news and social activity, all in one feed.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${fontSans.variable} ${fontSerif.variable} ${fontMono.variable} ${fontDisplay.variable} h-full antialiased`}
      suppressHydrationWarning
    >
      <body className="min-h-full flex flex-col bg-background text-foreground font-sans">
        <ThemeProvider attribute="class" defaultTheme="system" enableSystem>
          <Header />
          {children}
        </ThemeProvider>
      </body>
    </html>
  );
}
