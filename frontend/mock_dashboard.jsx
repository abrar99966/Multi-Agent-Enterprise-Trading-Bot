import React from 'react';

const Dashboard = () => {
  const recommendations = [
    {
      id: 1,
      symbol: 'RELIANCE',
      side: 'BUY',
      price: 2540.50,
      target: 2650.00,
      sl: 2480.00,
      confidence: 0.85,
      reasoning: 'Strong bullish breakout. Institutional activity detected.'
    }
  ];

  return (
    <div className="p-8 bg-gray-50 min-h-screen font-sans">
      <header className="mb-8">
        <h1 className="text-3xl font-bold text-gray-900">AI Trading Assistant</h1>
        <p className="text-gray-600">Enterprise-grade market intelligence</p>
      </header>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
        <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
          <h3 className="text-sm font-medium text-gray-500 uppercase tracking-wider">Total Capital</h3>
          <p className="text-2xl font-bold text-gray-900">₹10,00,000</p>
        </div>
        <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
          <h3 className="text-sm font-medium text-gray-500 uppercase tracking-wider">Open Positions</h3>
          <p className="text-2xl font-bold text-gray-900">2</p>
        </div>
        <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
          <h3 className="text-sm font-medium text-gray-500 uppercase tracking-wider">Daily PnL</h3>
          <p className="text-2xl font-bold text-green-600">+₹12,450</p>
        </div>
      </div>

      <section>
        <h2 className="text-xl font-semibold mb-4">Pending Approvals</h2>
        <div className="space-y-4">
          {recommendations.map(rec => (
            <div key={rec.id} className="bg-white p-6 rounded-xl shadow-sm border border-gray-100 flex flex-col md:flex-row justify-between items-start md:items-center">
              <div className="flex-1">
                <div className="flex items-center gap-3 mb-2">
                  <span className="text-lg font-bold">{rec.symbol}</span>
                  <span className={`px-2 py-1 rounded text-xs font-bold ${rec.side === 'BUY' ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}`}>
                    {rec.side}
                  </span>
                  <span className="text-sm text-gray-500">Confidence: {(rec.confidence * 100).toFixed(0)}%</span>
                </div>
                <p className="text-gray-600 text-sm mb-4">{rec.reasoning}</p>
                <div className="grid grid-cols-3 gap-4 text-sm">
                  <div><span className="text-gray-500">Entry:</span> <span className="font-medium">₹{rec.price}</span></div>
                  <div><span className="text-gray-500">Target:</span> <span className="font-medium text-green-600">₹{rec.target}</span></div>
                  <div><span className="text-gray-500">SL:</span> <span className="font-medium text-red-600">₹{rec.sl}</span></div>
                </div>
              </div>
              <div className="flex gap-3 mt-4 md:mt-0">
                <button className="px-4 py-2 border border-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50">Reject</button>
                <button className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700">Approve & Execute</button>
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
};

export default Dashboard;
