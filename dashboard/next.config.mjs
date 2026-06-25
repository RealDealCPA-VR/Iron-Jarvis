/** @type {import('next').NextConfig} */
const nextConfig = {
  // Lint is run separately; never let it block the production build proof.
  eslint: { ignoreDuringBuilds: true },
};

export default nextConfig;
