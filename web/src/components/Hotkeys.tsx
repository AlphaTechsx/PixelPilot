import { motion, useMotionValue, useSpring, useTransform } from 'framer-motion';
import './Hotkeys.css';

const hotkeys = [
    { keys: ['Ctrl', 'Shift', 'Z'], action: 'Toggle Click-Through' },
    { keys: ['Ctrl', 'Shift', 'X'], action: 'Stop Execution' },
    { keys: ['Ctrl', 'Shift', 'Q'], action: 'Quit Application' },
];

const TiltCard = ({ children }: { children: React.ReactNode }) => {
    const x = useMotionValue(0);
    const y = useMotionValue(0);

    const mouseX = useSpring(x, { stiffness: 150, damping: 15 });
    const mouseY = useSpring(y, { stiffness: 150, damping: 15 });

    const rotateX = useTransform(mouseY, [-0.5, 0.5], ["15deg", "-15deg"]);
    const rotateY = useTransform(mouseX, [-0.5, 0.5], ["-15deg", "15deg"]);

    const handleMouseMove = (e: React.MouseEvent<HTMLDivElement>) => {
        const rect = e.currentTarget.getBoundingClientRect();
        const width = rect.width;
        const height = rect.height;
        const mouseXVal = e.clientX - rect.left;
        const mouseYVal = e.clientY - rect.top;
        const xPct = mouseXVal / width - 0.5;
        const yPct = mouseYVal / height - 0.5;
        x.set(xPct);
        y.set(yPct);
    };

    const handleMouseLeave = () => {
        x.set(0);
        y.set(0);
    };

    return (
        <motion.div
            onMouseMove={handleMouseMove}
            onMouseLeave={handleMouseLeave}
            style={{
                rotateX,
                rotateY,
                transformStyle: "preserve-3d",
            }}
            className="tilt-card"
        >
            <div style={{ transform: "translateZ(50px)" }}>
                {children}
            </div>
        </motion.div>
    );
};

export const Hotkeys = () => {
    return (
        <section id="hotkeys" className="hotkeys-section">
             <div className="container">
                <h2 className="section-title">CONTROL MATRIX</h2>
                <div className="hotkeys-grid">
                    {hotkeys.map((item, i) => (
                        <TiltCard key={i}>
                            <div className="hk-card-inner">
                                <div className="keys-row">
                                    {item.keys.map((k) => (
                                        <div key={k} className="key-cap">{k}</div>
                                    ))}
                                </div>
                                <p className="hk-action">{item.action}</p>
                            </div>
                        </TiltCard>
                    ))}
                </div>
             </div>
        </section>
    );
};
