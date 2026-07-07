import asyncio
from backend.app.services.ai_service import ai_service
from backend.app.services.trade_service import trade_service
from backend.app.schemas.trade import TradeApproval, TradeRecommendationCreate

async def test_workflow():
    print("Testing AI Recommendation Generation...")
    symbol = "RELIANCE"
    rec_data = await ai_service.get_trade_recommendation(symbol)
    print(f"Generated Recommendation: {rec_data['symbol']} - {rec_data['side']} - Qty: {rec_data['quantity']}")
    assert rec_data['symbol'] == symbol
    assert rec_data['quantity'] > 0
    
    print("\nTesting Trade Creation and Notification...")
    rec = await trade_service.create_recommendation(TradeRecommendationCreate(**rec_data))
    assert rec['id'] is not None
    assert rec['status'].value == "pending_approval"
    
    print("\nTesting Human Approval...")
    approval = TradeApproval(approved=True, notes="Looks good")
    success = await trade_service.process_approval(rec['id'], approval)
    assert success is True
    
    pending = await trade_service.get_pending_recommendations()
    assert all(r['id'] != rec['id'] for r in pending)
    print("Workflow Test Passed!")

if __name__ == "__main__":
    asyncio.run(test_workflow())
