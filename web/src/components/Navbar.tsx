import { motion, useScroll, useTransform } from 'framer-motion';
import { Logo } from './Logo';
import { Magnetic } from './Magnetic';
import './Navbar.css';

export const Navbar = () => {
    const { scrollY } = useScroll();
    const opacity = useTransform(scrollY, [0, 100], [0, 1]);
    const y = useTransform(scrollY, [0, 100], [-100, 0]);

    const scrollToSection = (id: string) => {
        document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' });
    };

    return (
        <>
            {/* Top Logo (Static initially, then fades out or ignored) */}
            <motion.div 
                className="fixed-logo-container"
                style={{ opacity: useTransform(scrollY, [0, 50], [1, 0]), pointerEvents: useTransform(scrollY, [0, 50], ['auto', 'none']) }}
            >
                <Logo size={40} />
            </motion.div>

            {/* Floating Navbar Pill */}
            <motion.nav 
                className="navbar-pill"
                style={{ opacity, y }}
            >
                <div className="navbar-inner">
                    <Magnetic>
                        <button onClick={() => scrollToSection('features')} className="nav-link">Capabilities</button>
                    </Magnetic>
                    <Magnetic>
                        <button onClick={() => scrollToSection('quickstart')} className="nav-link">Install</button>
                    </Magnetic>
                    <div className="nav-separator" />
                    <Logo size={24} />
                    <div className="nav-separator" />
                    <Magnetic>
                        <button onClick={() => scrollToSection('hotkeys')} className="nav-link">Controls</button>
                    </Magnetic>
                    <Magnetic>
                        <a href="https://github.com" target="_blank" className="nav-link">GitHub</a>
                    </Magnetic>
                </div>
            </motion.nav>
        </>
    );
};
