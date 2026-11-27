import { Route, Routes } from 'react-router-dom'
import Navbar from './components/Navbar'
import ChatWidget from './components/ChatWidget'
import Home from './pages/Home'
import Products from './pages/Products'
import ProductDetail from './pages/ProductDetail'
import Drivers from './pages/Drivers'
import Assets from './pages/Assets'
import WebDriver from './pages/WebDriver'
import Checkout from './pages/Checkout'
import Workbench from './pages/Workbench'
import FlashSale from './pages/FlashSale'
import Settings from './pages/Settings'
import Memory from './pages/Memory'
import Nlu from './pages/Nlu'
import Footer from './components/Footer'
import { CartProvider } from './store'

export default function App() {
  return (
    <CartProvider>
      <Navbar />
      <main className="container">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/products" element={<Products />} />
          <Route path="/products/:ident" element={<ProductDetail />} />
          <Route path="/drivers" element={<Drivers />} />
          <Route path="/assets" element={<Assets />} />
          <Route path="/webdriver" element={<WebDriver />} />
          <Route path="/checkout" element={<Checkout />} />
          <Route path="/workbench" element={<Workbench />} />
          <Route path="/flash" element={<FlashSale />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/memory" element={<Memory />} />
          <Route path="/nlu" element={<Nlu />} />
        </Routes>
      </main>
      <Footer />
      <ChatWidget />
    </CartProvider>
  )
}
