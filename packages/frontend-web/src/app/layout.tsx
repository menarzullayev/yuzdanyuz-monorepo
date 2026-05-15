import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'YuzDanYuz — DTM Simulator',
  description: 'O\'zbekistondagi DTM simulator va ta\'lim platformasi',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="uz">
      <head>
        <meta name="theme-color" content="#2563eb" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
      </head>
      <body>
        {children}
      </body>
    </html>
  );
}
