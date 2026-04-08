import { motion } from 'framer-motion';
import { Link } from 'react-router-dom';
import './QuickStart.css';
import { Magnetic } from './Magnetic';

const commands = [
    { text: "Install the PixelPilot desktop app", output: "The MSI registers the deep link and stages the runtime helpers." },
    { text: "Choose hosted sign-in or direct mode", output: "Sign in through the browser or paste your own Gemini API key." },
    { text: "Review operation modes", output: "Choose Guidance, Safe, or Auto based on the level of autonomy you want." },
    { text: "Open docs and backend notes", output: "See hosted auth, backend setup, and troubleshooting details." }
];

export const QuickStart = () => {
    return (
        <section id="quickstart" className="quickstart-section">
            <div className="container">
                <div className="qs-content">
                    <h2 className="qs-title">GET STARTED</h2>
                    <p className="qs-subtitle">A simple path from install to first command.</p>
                </div>

                <div className="terminal-window">
                    <div className="terminal-header">
                        <div className="t-dot red" />
                        <div className="t-dot yellow" />
                        <div className="t-dot green" />
                        <span className="t-title">pixelpilot.io / launch-path</span>
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
