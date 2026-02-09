import { motion } from 'framer-motion';
import './QuickStart.css';
import { Magnetic } from './Magnetic';

const commands = [
    { text: "git clone https://github.com/pixelpilot/agent", output: "Cloning into 'agent'..." },
    { text: "cd agent && python install.py", output: "Installing dependencies... Done." },
    { text: "python src/main.py", output: "PixelPilot v1.0.0 Online. Listening..." }
];

export const QuickStart = () => {
    return (
        <section id="quickstart" className="quickstart-section">
            <div className="container">
                <div className="qs-content">
                    <h2 className="qs-title">INITIALIZATION</h2>
                    <p className="qs-subtitle">Three steps to authority.</p>
                </div>
                
                <div className="terminal-window">
                    <div className="terminal-header">
                        <div className="t-dot red" />
                        <div className="t-dot yellow" />
                        <div className="t-dot green" />
                        <span className="t-title">admin@pixelpilot:~</span>
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
                                    <span className="prompt">$</span>
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
                        <a href="#" className="docs-link">Read Full Documentation &rarr;</a>
                   </Magnetic>
                </div>
            </div>
        </section>
    );
};
