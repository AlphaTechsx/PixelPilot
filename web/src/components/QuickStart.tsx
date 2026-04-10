import { motion } from 'framer-motion';
import { Link } from 'react-router-dom';
import './QuickStart.css';
import { Magnetic } from './Magnetic';

const commands = [
    { text: "Install the desktop control room", output: "The installer wires up deep links and stages the runtime helpers." },
    { text: "Choose your connection path", output: "Use hosted sign-in or bring your own Gemini API key for direct mode." },
    { text: "Set the automation posture", output: "Pick Guidance, Safe, or Auto based on how much autonomy you want." },
    { text: "Open the field manual", output: "Review hosted auth, backend setup, and troubleshooting in one pass." }
];

export const QuickStart = () => {
    return (
        <section id="quickstart" className="quickstart-section">
            <div className="container">
                <div className="qs-content">
                    <h2 className="qs-title">GET STARTED</h2>
                    <p className="qs-subtitle">A clean runway from first install to first live command.</p>
                </div>

                <div className="terminal-window">
                    <div className="terminal-header">
                        <div className="t-dot red" />
                        <div className="t-dot yellow" />
                        <div className="t-dot green" />
                        <span className="t-title">operator://first-launch</span>
                    </div>
                    <div className="terminal-body">
                        {commands.map((cmd, i) => (
                            <motion.div
                                key={i}
                                initial={{ opacity: 0, x: -10 }}
                                whileInView={{ opacity: 1, x: 0 }}
                                viewport={{ once: true, margin: "-50px" }}
                                transition={{ delay: i * 0.8, duration: 0.5 }}
                                className="cmd-row"
                            >
                                <div className="cmd-line">
                                    <span className="prompt">&gt;</span>
                                    <span className="cmd-text">{cmd.text}</span>
                                </div>
                                <motion.div
                                    className="cmd-output"
                                    initial={{ opacity: 0 }}
                                    whileInView={{ opacity: 0.6 }}
                                    transition={{ delay: i * 0.8 + 0.4 }}
                                >
                                    {cmd.output}
                                </motion.div>
                            </motion.div>
                        ))}
                    </div>
                </div>

                <div className="qs-actions">
                    <Magnetic>
                        <Link to="/docs" className="docs-link">Open Documentation &rarr;</Link>
                    </Magnetic>
                </div>
            </div>
        </section>
    );
};
