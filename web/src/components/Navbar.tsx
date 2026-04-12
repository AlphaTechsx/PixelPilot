import { useState } from 'react';
import { motion, useScroll, useTransform } from 'framer-motion';
import { Link, useLocation } from 'react-router-dom';
import { Logo } from './Logo';
import { Magnetic } from './Magnetic';
import './Navbar.css';

export const Navbar = () => {
    const { scrollY } = useScroll();
    const opacity = useTransform(scrollY, [0, 100], [0, 1]);
    const y = useTransform(scrollY, [0, 100], [-100, 0]);
    const location = useLocation();
    const isHome = location.pathname === '/';
    const [menuState, setMenuState] = useState({ pathname: location.pathname, isOpen: false });
    const isMenuOpen = menuState.pathname === location.pathname && menuState.isOpen;
    const closeMenu = () => setMenuState({ pathname: location.pathname, isOpen: false });

    const scrollToSection = (id: string) => {
        document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' });
        closeMenu();
    };

    return (
        <>
            <motion.div 
                className="fixed-logo-container"
                style={{ opacity: useTransform(scrollY, [0, 50], [1, 0]), pointerEvents: useTransform(scrollY, [0, 50], ['auto', 'none']) }}
            >
                <Logo size={40} />
            </motion.div>

            <motion.nav 
                className="navbar-pill"
                style={{ opacity, y }}
            >
                <div className="navbar-inner">
                    <div className="navbar-brand">
                        <Logo size={24} />
                        <span>PixelPilot</span>
                    </div>

                    <button
                        type="button"
                        className="navbar-menu-toggle"
                        aria-label={isMenuOpen ? 'Close navigation menu' : 'Open navigation menu'}
                        aria-expanded={isMenuOpen}
                        onClick={() => setMenuState({ pathname: location.pathname, isOpen: !isMenuOpen })}
                    >
                        <span />
                        <span />
                        <span />
                    </button>

                    <div className={`navbar-links ${isMenuOpen ? 'open' : ''}`}>
                        {isHome ? (
                            <>
                                <Magnetic>
                                    <button onClick={() => scrollToSection('features')} className="nav-link">Why</button>
                                </Magnetic>
                                <Magnetic>
                                    <button onClick={() => scrollToSection('quickstart')} className="nav-link">Start</button>
                                </Magnetic>
                            </>
                        ) : (
                            <Magnetic>
                                <Link to="/" className="nav-link">Home</Link>
                            </Magnetic>
                        )}
                        {isHome && (
                            <Magnetic>
                                <button onClick={() => scrollToSection('hotkeys')} className="nav-link">Modes</button>
                            </Magnetic>
                        )}
                        <Magnetic>
                            <Link to="/docs" className="nav-link">Docs</Link>
                        </Magnetic>
                        <Magnetic>
                            <a
                                href="https://github.com/AlphaTechsx/PixelPilot"
                                target="_blank"
                                rel="noreferrer"
                                className="nav-link"
                                onClick={closeMenu}
                            >
                                GitHub
                            </a>
                        </Magnetic>
                    </div>
                </div>
            </motion.nav>
        </>
    );
};
