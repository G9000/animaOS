import { useState, useMemo, useRef } from "react";
import { Icons } from "./Icons";

interface TableNode {
  name: string;
  columns: Array<{ name: string; type: string; isPrimaryKey: boolean; isForeignKey: boolean }>;
  x: number;
  y: number;
  width: number;
  height: number;
}

interface Relationship {
  from: string;
  fromColumn: string;
  to: string;
  toColumn: string;
  type: "one-to-one" | "one-to-many" | "many-to-many";
}

interface ERDiagramProps {
  tables: Array<{ name: string; rowCount: number }>;
  onOpenTable: (_name: string) => void;
}

// Mock schema data - in real implementation, fetch from backend
const mockSchemas: Record<string, TableNode> = {
  users: {
    name: "users",
    x: 100,
    y: 100,
    width: 160,
    height: 140,
    columns: [
      { name: "id", type: "INTEGER", isPrimaryKey: true, isForeignKey: false },
      { name: "username", type: "TEXT", isPrimaryKey: false, isForeignKey: false },
      { name: "email", type: "TEXT", isPrimaryKey: false, isForeignKey: false },
      { name: "created_at", type: "DATETIME", isPrimaryKey: false, isForeignKey: false },
    ],
  },
  posts: {
    name: "posts",
    x: 400,
    y: 100,
    width: 160,
    height: 140,
    columns: [
      { name: "id", type: "INTEGER", isPrimaryKey: true, isForeignKey: false },
      { name: "user_id", type: "INTEGER", isPrimaryKey: false, isForeignKey: true },
      { name: "title", type: "TEXT", isPrimaryKey: false, isForeignKey: false },
      { name: "content", type: "TEXT", isPrimaryKey: false, isForeignKey: false },
    ],
  },
  comments: {
    name: "comments",
    x: 700,
    y: 100,
    width: 160,
    height: 120,
    columns: [
      { name: "id", type: "INTEGER", isPrimaryKey: true, isForeignKey: false },
      { name: "post_id", type: "INTEGER", isPrimaryKey: false, isForeignKey: true },
      { name: "content", type: "TEXT", isPrimaryKey: false, isForeignKey: false },
    ],
  },
  categories: {
    name: "categories",
    x: 400,
    y: 350,
    width: 160,
    height: 100,
    columns: [
      { name: "id", type: "INTEGER", isPrimaryKey: true, isForeignKey: false },
      { name: "name", type: "TEXT", isPrimaryKey: false, isForeignKey: false },
    ],
  },
  post_categories: {
    name: "post_categories",
    x: 400,
    y: 550,
    width: 160,
    height: 80,
    columns: [
      { name: "post_id", type: "INTEGER", isPrimaryKey: false, isForeignKey: true },
      { name: "category_id", type: "INTEGER", isPrimaryKey: false, isForeignKey: true },
    ],
  },
};

const mockRelationships: Relationship[] = [
  { from: "users", fromColumn: "id", to: "posts", toColumn: "user_id", type: "one-to-many" },
  { from: "posts", fromColumn: "id", to: "comments", toColumn: "post_id", type: "one-to-many" },
  { from: "posts", fromColumn: "id", to: "post_categories", toColumn: "post_id", type: "one-to-many" },
  { from: "categories", fromColumn: "id", to: "post_categories", toColumn: "category_id", type: "one-to-many" },
];

export function ERDiagram({ tables: _tables, onOpenTable }: ERDiagramProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [isDragging, setIsDragging] = useState(false);
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 });
  const svgRef = useRef<SVGSVGElement>(null);

  // Auto-layout nodes based on relationships
  const nodes = useMemo(() => {
    // Simple grid layout
    const nodeList = Object.values(mockSchemas);
    const cols = Math.ceil(Math.sqrt(nodeList.length));
    const spacing = 250;
    
    return nodeList.map((node, i) => ({
      ...node,
      x: (i % cols) * spacing + 50,
      y: Math.floor(i / cols) * 200 + 50,
    }));
  }, []);

  const handleMouseDown = (e: React.MouseEvent) => {
    if (e.target === svgRef.current) {
      setIsDragging(true);
      setDragStart({ x: e.clientX - pan.x, y: e.clientY - pan.y });
    }
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    if (isDragging) {
      setPan({
        x: e.clientX - dragStart.x,
        y: e.clientY - dragStart.y,
      });
    }
  };

  const handleMouseUp = () => {
    setIsDragging(false);
  };

  // Calculate connection paths
  const getConnectionPath = (from: TableNode, to: TableNode) => {
    const fromX = from.x + from.width / 2;
    const fromY = from.y + from.height / 2;
    const toX = to.x + to.width / 2;
    const toY = to.y + to.height / 2;

    // Simple curved path
    const midX = (fromX + toX) / 2;
    return `M ${fromX} ${fromY} Q ${midX} ${fromY} ${midX} ${(fromY + toY) / 2} T ${toX} ${toY}`;
  };

  return (
    <div className="relative">
      <button
        onClick={() => setIsOpen(true)}
        className="flex items-center gap-1 px-2 py-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
      >
        <Icons.Sitemap />
        ER Diagram
      </button>

      {isOpen && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="w-[900px] h-[600px] bg-card border border-border rounded-lg flex flex-col">
            {/* Header */}
            <div className="px-4 py-3 border-b border-border flex items-center justify-between">
              <div>
                <h3 className="text-sm font-medium">Database Schema (ER Diagram)</h3>
                <p className="text-[10px] text-muted-foreground">Drag to pan • Scroll to zoom</p>
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => setZoom((z) => Math.max(0.5, z - 0.1))}
                  className="p-1 text-muted-foreground hover:text-foreground"
                >
                  -
                </button>
                <span className="text-xs w-12 text-center">{(zoom * 100).toFixed(0)}%</span>
                <button
                  onClick={() => setZoom((z) => Math.min(2, z + 0.1))}
                  className="p-1 text-muted-foreground hover:text-foreground"
                >
                  +
                </button>
                <button
                  onClick={() => { setZoom(1); setPan({ x: 0, y: 0 }); }}
                  className="px-2 py-1 text-xs text-muted-foreground hover:text-foreground"
                >
                  Reset
                </button>
                <button onClick={() => setIsOpen(false)} className="p-1 text-muted-foreground/50 hover:text-foreground ml-2">
                  <Icons.X />
                </button>
              </div>
            </div>

            {/* Diagram */}
            <div className="flex-1 overflow-hidden bg-input relative">
              <svg
                ref={svgRef}
                className="w-full h-full cursor-grab active:cursor-grabbing"
                onMouseDown={handleMouseDown}
                onMouseMove={handleMouseMove}
                onMouseUp={handleMouseUp}
                onMouseLeave={handleMouseUp}
                onWheel={(e) => {
                  e.preventDefault();
                  setZoom((z) => Math.max(0.5, Math.min(2, z - e.deltaY * 0.001)));
                }}
              >
                <g transform={`translate(${pan.x}, ${pan.y}) scale(${zoom})`}>
                  {/* Connections */}
                  {mockRelationships.map((rel, i) => {
                    const fromNode = nodes.find((n) => n.name === rel.from);
                    const toNode = nodes.find((n) => n.name === rel.to);
                    if (!fromNode || !toNode) return null;

                    return (
                      <g key={i}>
                        <path
                          d={getConnectionPath(fromNode, toNode)}
                          fill="none"
                          stroke="#3b82f6"
                          strokeWidth="2"
                          strokeDasharray={rel.type === "many-to-many" ? "5,5" : undefined}
                          markerEnd="url(#arrowhead)"
                        />
                      </g>
                    );
                  })}

                  {/* Arrow marker */}
                  <defs>
                    <marker
                      id="arrowhead"
                      markerWidth="10"
                      markerHeight="7"
                      refX="9"
                      refY="3.5"
                      orient="auto"
                    >
                      <polygon points="0 0, 10 3.5, 0 7" fill="#3b82f6" />
                    </marker>
                  </defs>

                  {/* Tables */}
                  {nodes.map((node) => (
                    <g key={node.name} transform={`translate(${node.x}, ${node.y})`}>
                      {/* Table box */}
                      <rect
                        width={node.width}
                        height={node.height}
                        rx="6"
                        fill="#1a1a1a"
                        stroke="#3b82f6"
                        strokeWidth="2"
                        className="cursor-pointer hover:stroke-primary transition-colors"
                        onClick={() => onOpenTable(node.name)}
                      />

                      {/* Table name */}
                      <rect
                        x="0"
                        y="0"
                        width={node.width}
                        height="28"
                        rx="6"
                        fill="#3b82f620"
                      />
                      <text
                        x={node.width / 2}
                        y="18"
                        textAnchor="middle"
                        fill="#3b82f6"
                        fontSize="12"
                        fontWeight="bold"
                        className="pointer-events-none"
                      >
                        {node.name}
                      </text>

                      {/* Columns */}
                      {node.columns.map((col, i) => (
                        <g key={col.name} transform={`translate(10, ${35 + i * 22})`}>
                          {/* Key icon */}
                          {col.isPrimaryKey && (
                            <text x="0" y="12" fill="#f59e0b" fontSize="10">
                              🔑
                            </text>
                          )}
                          {col.isForeignKey && !col.isPrimaryKey && (
                            <text x="0" y="12" fill="#10b981" fontSize="10">
                              🔗
                            </text>
                          )}

                          {/* Column name */}
                          <text
                            x={col.isPrimaryKey || col.isForeignKey ? "20" : "0"}
                            y="12"
                            fill="#e5e5e5"
                            fontSize="11"
                            className="pointer-events-none"
                          >
                            {col.name}
                          </text>

                          {/* Type */}
                          <text
                            x={node.width - 20}
                            y="12"
                            textAnchor="end"
                            fill="#6b7280"
                            fontSize="9"
                            className="pointer-events-none"
                          >
                            {col.type}
                          </text>
                        </g>
                      ))}
                    </g>
                  ))}
                </g>
              </svg>

              {/* Legend */}
              <div className="absolute bottom-4 left-4 p-3 bg-card border border-border rounded-lg text-xs space-y-2">
                <div className="flex items-center gap-2">
                  <span>🔑</span>
                  <span>Primary Key</span>
                </div>
                <div className="flex items-center gap-2">
                  <span>🔗</span>
                  <span>Foreign Key</span>
                </div>
                <div className="flex items-center gap-2">
                  <div className="w-4 h-0.5 bg-blue-500" />
                  <span>Relationship</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
