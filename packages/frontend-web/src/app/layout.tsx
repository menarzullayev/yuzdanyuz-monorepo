import type { Metadata } from 'next';

import { NextIntlClientProvider } from 'next-intl';
import { getLocale, getMessages } from 'next-intl/server';

<<<<<<< Updated upstream
import { Providers } from './providers';

=======
>>>>>>> Stashed changes
import './globals.css';
import { Providers } from './providers';

export const metadata: Metadata = {
  title: 'YuzDanYuz — Milliy Sertifikat',
<<<<<<< Updated upstream
  description:
    "O'zbekiston DTM imtihoniga tayyorgarlik + AI yordamchi + B2B maktab platformasi",
=======
  description: "O'zbekiston DTM imtihoniga tayyorgarlik + AI yordamchi + B2B maktab platformasi",
>>>>>>> Stashed changes
};

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const locale = await getLocale();
  const messages = await getMessages();

  return (
    <html lang={locale} className="h-full antialiased">
      <body className="flex min-h-full flex-col">
        <NextIntlClientProvider locale={locale} messages={messages}>
          <Providers>{children}</Providers>
        </NextIntlClientProvider>
      </body>
    </html>
  );
}
