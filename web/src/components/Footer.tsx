import './Footer.css';
import { Magnetic } from './Magnetic';
import { Logo } from './Logo';

export const Footer = () => {
    return (
        <footer className="big-footer">
            <div className="container footer-inner">
                <div className="footer-callout">
                    <h2>RUN THE<br/>DESKTOP LIKE MISSION CONTROL</h2>
                    <Magnetic strength={0.5}>
                        <div className="scroll-top-btn" onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}>
                            &uarr;
                        </div>
                    </Magnetic>
                </div>
                
                <div className="footer-bottom">
                    <div className="footer-brand">
                        <Logo size={32} />
                        <span>PIXELPILOT &copy; {new Date().getFullYear()}</span>
                    </div>
                    
                    <div className="footer-links">
                        <a href="https://github.com/AlphaTechsx/PixelPilot/blob/main/LICENSE" target="_blank" rel="noreferrer">License</a>
                        <a href="https://github.com/AlphaTechsx/PixelPilot" target="_blank" rel="noreferrer">GitHub</a>
                        <a href="https://www.linkedin.com/company/pixelpilotai/" target="_blank" rel="noreferrer">LinkedIn</a>
                        <a href="/docs">Docs</a>
                    </div>
                </div>
            </div>
        </footer>
    );
};
