import { motion } from 'framer-motion';
import { useRef, useState } from 'react';
import type { ReactNode, MouseEvent } from 'react';

interface MagneticProps {
    children: ReactNode;
    strength?: number; // How strong the pull is
}

export const Magnetic = ({ children, strength = 0.5 }: MagneticProps) => {
    const ref = useRef<HTMLDivElement>(null);
    const [position, setPosition] = useState({ x: 0, y: 0 });

    const handleMouse = (e: MouseEvent) => {
        const { clientX, clientY } = e;
        const { height, width, left, top } = ref.current!.getBoundingClientRect();
        
        const middleX = clientX - (left + width / 2);
        const middleY = clientY - (top + height / 2);
        
        setPosition({ x: middleX * strength, y: middleY * strength });
    };

    const reset = () => {
        setPosition({ x: 0, y: 0 });
    };

    return (
        <motion.div
            ref={ref}
            onMouseMove={handleMouse}
            onMouseLeave={reset}
            animate={{ x: position.x, y: position.y }}
            transition={{ type: "spring", stiffness: 150, damping: 15, mass: 0.1 }}
            style={{ display: 'inline-block' }}
        >
            {children}
        </motion.div>
    );
};
