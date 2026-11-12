import { useTranslations } from 'next-intl';

export type Pose = 'salom' | 'kozini-yopgan' | 'notogri-javob' | 'xarita' | 'konfetti';

const FILES: Record<Pose, string> = {
  salom: '01-salom',
  'kozini-yopgan': '02-kozini-yopgan',
  'notogri-javob': '05-notogri-javob',
  xarita: '15-xarita',
  konfetti: '17-konfetti',
};

export function Mascot({ pose, size = 140 }: { pose: Pose; size?: number }) {
  const t = useTranslations('mascot');
  return (
    // eslint-disable-next-line @next/next/no-img-element -- static SVG, nothing to optimise
    <img
      key={pose}
      src={`/mascot/${FILES[pose]}.svg`}
      alt={t(pose)}
      width={size}
      height={size}
      className="mascot-swap mx-auto block h-auto"
      style={{ width: size, maxWidth: '100%' }}
    />
  );
}
