// The NTNXVision mark (Cube Iris): the app icon's art without its tile, for the dark command
// bar. Same drawing as build/art/icon.svg; change both together (npm run icons).
import React from 'react';

export function BrandMark({ size = 20 }: { size?: number }) {
  const eye = 'M112 512 C312 218.7 712 218.7 912 512 C712 805.3 312 805.3 112 512 Z';
  return (
    <svg className="brand-mark" width={size} height={size} viewBox="112 112 800 800" aria-hidden="true" focusable="false">
      <defs>
        <clipPath id="brand-mark-eye">
          <path d={eye} />
        </clipPath>
      </defs>
      <path d={eye} fill="#eae8f8" />
      <circle cx="512" cy="512" r="190" fill="#5b3fd9" clipPath="url(#brand-mark-eye)" />
      <polygon points="415,456 512,400 609,456 512,512" fill="#ffffff" />
      <polygon points="415,456 512,512 512,624 415,568" fill="#bbaaff" />
      <polygon points="512,512 609,456 609,568 512,624" fill="#9c86ff" />
    </svg>
  );
}
