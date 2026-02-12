import './App.css';
import { BrowserRouter, Route, Routes } from 'react-router-dom';
import { Navbar } from './components/Navbar';
import ScrollToTop from './components/ScrollToTop';
import { AuroraBackground } from './components/AuroraBackground';
import { HomePage } from './pages/HomePage';
import { DocsPage } from './pages/DocsPage';

function App() {
  return (
    <BrowserRouter>
      <ScrollToTop />
      <AuroraBackground />
      <Navbar />
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/docs" element={<DocsPage />} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;
