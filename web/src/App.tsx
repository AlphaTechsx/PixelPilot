import './App.css';
import { Suspense, lazy } from 'react';
import { MotionConfig } from 'framer-motion';
import { BrowserRouter, Route, Routes } from 'react-router-dom';
import { Navbar } from './components/Navbar';
import ScrollToTop from './components/ScrollToTop';
import { AuroraBackground } from './components/AuroraBackground';

const HomePage = lazy(() => import('./pages/HomePage').then((module) => ({ default: module.HomePage })));
const DocsPage = lazy(() => import('./pages/DocsPage').then((module) => ({ default: module.DocsPage })));
const AuthPage = lazy(() => import('./pages/AuthPage').then((module) => ({ default: module.AuthPage })));
const AuthCompletePage = lazy(() => import('./pages/AuthCompletePage').then((module) => ({ default: module.AuthCompletePage })));

function App() {
  return (
    <MotionConfig reducedMotion="user">
      <BrowserRouter>
        <ScrollToTop />
        <AuroraBackground />
        <Navbar />
        <Suspense fallback={<main className="route-loading">Loading PixelPilot...</main>}>
          <Routes>
            <Route path="/" element={<HomePage />} />
            <Route path="/docs" element={<DocsPage />} />
            <Route path="/auth/sign-in" element={<AuthPage mode="signin" />} />
            <Route path="/auth/sign-up" element={<AuthPage mode="signup" />} />
            <Route path="/auth/complete" element={<AuthCompletePage />} />
          </Routes>
        </Suspense>
      </BrowserRouter>
    </MotionConfig>
  );
}

export default App;
