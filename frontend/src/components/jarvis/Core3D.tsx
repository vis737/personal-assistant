import { Canvas, useFrame } from "@react-three/fiber";
import { Float, useTexture } from "@react-three/drei";
import { Suspense, useMemo, useRef } from "react";
import earthTexture from "@/assets/earth.jpg";

import * as THREE from "three";

const CYAN = "#5fe3ff";
const GOLD = "#ffc14d";
const RED = "#d8342b";

function Ring({ r, w = 0.02, speed, tilt = [0, 0, 0], color = CYAN, arc = Math.PI * 2, opacity = 0.9 }: {
  r: number; w?: number; speed: number; tilt?: [number, number, number]; color?: string; arc?: number; opacity?: number;
}) {
  const ref = useRef<THREE.Mesh>(null);
  useFrame((_, d) => { if (ref.current) ref.current.rotation.z += speed * Math.min(d, 0.05); });
  return (
    <group rotation={tilt}>
      <mesh ref={ref}>
        <ringGeometry args={[r, r + w, 128, 1, 0, arc]} />
        <meshBasicMaterial color={color} transparent opacity={opacity} side={THREE.DoubleSide} toneMapped={false} blending={THREE.AdditiveBlending} />
      </mesh>
    </group>
  );
}

function Ticks({ r, count, speed }: { r: number; count: number; speed: number }) {
  const ref = useRef<THREE.Group>(null);
  useFrame((_, d) => { if (ref.current) ref.current.rotation.z += speed * Math.min(d, 0.05); });
  const items = useMemo(() => Array.from({ length: count }, (_, i) => (i / count) * Math.PI * 2), [count]);
  return (
    <group ref={ref}>
      {items.map((a, i) => (
        <mesh key={i} position={[Math.cos(a) * r, Math.sin(a) * r, 0]} rotation={[0, 0, a]}>
          <planeGeometry args={[i % 5 === 0 ? 0.14 : 0.06, 0.015]} />
          <meshBasicMaterial color={i % 5 === 0 ? GOLD : CYAN} toneMapped={false} />
        </mesh>
      ))}
    </group>
  );
}

function Globe({ level }: { level: React.MutableRefObject<number> }) {
  const earth = useRef<THREE.Group>(null);
  const halo = useRef<THREE.Mesh>(null);
  const map = useTexture(earthTexture);
  map.colorSpace = THREE.SRGBColorSpace;
  map.anisotropy = 8;
  useFrame((s, d) => {
    const dt = Math.min(d, 0.05);
    if (earth.current) earth.current.rotation.y += dt * 0.15;
    if (halo.current) halo.current.scale.setScalar(1 + level.current * 0.08 + Math.sin(s.clock.elapsedTime * 2) * 0.01);
  });
  return (
    <group rotation={[0.41, 0, 0]}>
      <group ref={earth} rotation={[0, -Math.PI / 2, 0]}>
        <mesh>
          <sphereGeometry args={[1.3, 96, 96]} />
          <meshBasicMaterial map={map} toneMapped={false} />
        </mesh>
      </group>
      <mesh ref={halo}>
        <sphereGeometry args={[1.325, 64, 64]} />
        <meshBasicMaterial color={CYAN} transparent opacity={0.14} side={THREE.BackSide} blending={THREE.AdditiveBlending} depthWrite={false} toneMapped={false} />
      </mesh>
    </group>
  );
}

function Rig() {
  useFrame((s, delta) => {
    const damping = 1 - Math.exp(-2.4 * Math.min(delta, 0.05));
    s.camera.position.x = THREE.MathUtils.lerp(s.camera.position.x, s.pointer.x * 1.2, damping);
    s.camera.position.y = THREE.MathUtils.lerp(s.camera.position.y, s.pointer.y * 0.8, damping);
    s.camera.lookAt(0, 0, 0);
  });
  return null;
}

export function Core3D({ level }: { level: React.MutableRefObject<number> }) {
  return (
    <Canvas camera={{ position: [0, 0, 7.5], fov: 50 }} dpr={[1, 2]} gl={{ alpha: true, antialias: true, toneMapping: THREE.NoToneMapping }}>
      <ambientLight intensity={1.3} />
      <directionalLight position={[0, 0.6, 8]} intensity={1.2} />
      <directionalLight position={[4, 3, 5]} intensity={0.4} />
      <directionalLight position={[-3, -1, 5]} intensity={0.5} color="#bfefff" />
      <pointLight position={[-4, -1, 2]} intensity={7} color={CYAN} />



      <Rig />
      <Float speed={1.2} rotationIntensity={0.15} floatIntensity={0.3}>
        <Suspense fallback={null}><Globe level={level} /></Suspense>
        <Ring r={1.7} w={0.03} speed={0.6} arc={Math.PI * 1.4} />
        <Ring r={1.9} w={0.08} speed={-0.3} color={GOLD} arc={Math.PI * 0.5} opacity={0.8} />
        <Ring r={2.2} w={0.015} speed={0.15} />
        <Ticks r={2.45} count={72} speed={-0.08} />
        <Ring r={2.75} w={0.04} speed={0.4} arc={Math.PI * 0.35} color={RED} />
        <Ring r={2.75} w={0.04} speed={0.4} arc={Math.PI * 0.8} tilt={[0, 0, Math.PI]} />
        <Ring r={2.0} w={0.01} speed={0.9} tilt={[Math.PI / 2.6, 0.3, 0]} />
        <Ring r={2.3} w={0.01} speed={-0.7} tilt={[-Math.PI / 2.4, -0.4, 0]} color={GOLD} />
      </Float>
    </Canvas>
  );
}
